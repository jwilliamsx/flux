import streamlit as st
import pandas as pd
import sqlite3
import os
import folium
import requests
from streamlit_folium import st_folium

# --- FUNÇÕES ADICIONAIS ---

@st.cache_data(ttl=3600)  # Cache de 1 hora para não sobrecarregar a API
def buscar_postos_gasolina(lat_min, lon_min, lat_max, lon_max):
    """Busca postos de gasolina no OpenStreetMap via Overpass API."""
    overpass_url = "http://overpass-api.de/api/interpreter"
    
    # Adicionando uma margem maior (aprox 5km) para garantir a captura
    margin = 0.05 
    
    # Ordenando lat/lon para garantir que a query receba (min, min, max, max)
    s, n = min(lat_min, lat_max) - margin, max(lat_min, lat_max) + margin
    w, e = min(lon_min, lon_max) - margin, max(lon_min, lon_max) + margin

    query = f"""
    [out:json][timeout:25];
    node["amenity"="fuel"]({s},{w},{n},{e});
    out body;
    """
    
    headers = {
        'User-Agent': 'FluxAccidentAnalysis/1.0 (Streamlit App)'
    }
    
    try:
        response = requests.get(overpass_url, params={'data': query}, headers=headers, timeout=20)
        if response.status_code == 200:
            return response.json().get('elements', [])
        else:
            return f"Erro API: {response.status_code}"
    except Exception as e:
        return f"Erro de conexão: {e}"

# Configurações da Página
st.set_page_config(page_title="Flux - Análise de Acidentes", layout="wide", page_icon="🚗")

# --- 1. FUNÇÕES DE BANCO DE DADOS ---

def get_connection():
    """Retorna uma conexão com o banco de dados SQLite."""
    os.makedirs("data", exist_ok=True)
    return sqlite3.connect("data/acidentes.db", check_same_thread=False)

def criar_banco_se_nao_existir(force_update=False):
    """
    Carrega os CSVs, padroniza, combina e insere no SQLite.
    Só roda se o banco não existir ou se force_update=True.
    """
    if os.path.exists("data/acidentes.db") and not force_update:
        return

    arquivos = {
        2025: "data/datatran2025.csv",
        2026: "data/datatran2026.csv"
    }
    
    dfs = []
    
    progress_bar = st.sidebar.progress(0)
    status_text = st.sidebar.empty()
    
    for i, (ano, path) in enumerate(arquivos.items()):
        status_text.text(f"Carregando dados de {ano}...")
        try:
            # Lendo CSV com tratamento de erro
            df_temp = pd.read_csv(path, sep=';', encoding='latin1', low_memory=False)
            
            # Padronização de colunas (lowercase e strip)
            df_temp.columns = [col.lower().strip() for col in df_temp.columns]
            
            # Adicionando coluna de ano para diferenciar os datasets
            df_temp['ano'] = ano
            
            # Limpeza e conversão de tipos
            df_temp['municipio'] = df_temp['municipio'].astype(str).str.upper()
            for col in ['latitude', 'longitude', 'km', 'br']:
                df_temp[col] = pd.to_numeric(df_temp[col].astype(str).str.replace(',', '.'), errors='coerce')
            
            # Remover coordenadas nulas para o funcionamento do mapa
            df_temp = df_temp.dropna(subset=['latitude', 'longitude'])
            
            dfs.append(df_temp)
            progress_bar.progress((i + 1) * 45)
            
        except FileNotFoundError:
            st.error(f"Erro: Arquivo {path} não encontrado!")
            continue
        except Exception as e:
            st.error(f"Erro inesperado ao processar {path}: {e}")
            continue

    if dfs:
        status_text.text("Consolidando dados no banco de dados...")
        df_final = pd.concat(dfs, ignore_index=True)
        
        conn = get_connection()
        # Salva no banco de dados (substitui se existir)
        df_final.to_sql("acidentes", conn, if_exists="replace", index=False)
        conn.close()
        
        status_text.text("Banco de dados pronto!")
        progress_bar.empty()
        st.sidebar.success("Dados carregados com sucesso!")
    else:
        st.error("Não foi possível carregar nenhum dado para o banco.")

@st.cache_data
def consultar_dados(query):
    """Executa uma consulta SQL e retorna um DataFrame."""
    try:
        conn = get_connection()
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        # Pós-processamento: Extração da hora para análise temporal
        if 'horario' in df.columns:
            df['horario_hora'] = pd.to_datetime(df['horario'], format='%H:%M:%S', errors='coerce').dt.hour
            df = df.dropna(subset=['horario_hora'])
            df['horario_hora'] = df['horario_hora'].astype(int)
            
        return df
    except Exception as e:
        st.error(f"Erro ao consultar banco: {e}")
        return pd.DataFrame()

# --- 2. LÓGICA DE INTERFACE E FILTROS ---

st.sidebar.title("🛠️ Configurações")

# Botão para atualizar o banco manualmente
if st.sidebar.button("🔄 Atualizar Base de Dados"):
    criar_banco_se_nao_existir(force_update=True)
    st.cache_data.clear()

# Garante que o banco existe no início da execução
criar_banco_se_nao_existir()

# Filtros Globais carregados do banco
try:
    anos_disponiveis = sorted(consultar_dados("SELECT DISTINCT ano FROM acidentes")['ano'].tolist())
except:
    anos_disponiveis = []

if not anos_disponiveis:
    st.warning("Nenhum dado disponível. Verifique os arquivos CSV e clique em Atualizar.")
    st.stop()

filtro_ano = st.sidebar.multiselect("Selecione os Anos", options=anos_disponiveis, default=anos_disponiveis)

# Query dinâmica baseada nos anos selecionados (filtrando Pernambuco por padrão como no original)
query_base = f"SELECT * FROM acidentes WHERE uf = 'PE' AND ano IN ({','.join(map(str, filtro_ano))})"
df = consultar_dados(query_base)

if df.empty:
    st.info("Nenhum dado encontrado para os filtros selecionados.")
    st.stop()

st.title("🚗 Flux - Análise de Acidentes")

menu = ["Análise de Rota", "Análise Geral PE", "Comparativo Anual"]
modo_analise = st.sidebar.radio("Selecione o Modo de Análise", menu)

# --- 3. MODOS DE ANÁLISE ---

if modo_analise == "Análise de Rota":
    st.subheader("📍 Análise de Pontos Críticos por Rota")
    
    cidades_pe = sorted(df['municipio'].unique())
    
    col_orig, col_dest = st.sidebar.columns(2)
    origem = col_orig.selectbox("Origem", cidades_pe, index=cidades_pe.index("RECIFE") if "RECIFE" in cidades_pe else 0)
    destino = col_dest.selectbox("Destino", cidades_pe, index=cidades_pe.index("CARUARU") if "CARUARU" in cidades_pe else 1)

    if "analisar_clicado" not in st.session_state:
        st.session_state.analisar_clicado = False

    if st.sidebar.button("Analisar Rota"):
        st.session_state.analisar_clicado = True

    if st.session_state.analisar_clicado:
        if origem == destino:
            st.warning("Selecione cidades de origem e destino diferentes.")
        else:
            # Lógica para encontrar BR comum entre as cidades selecionadas
            brs_origem = set(df[df['municipio'] == origem]['br'].unique())
            brs_destino = set(df[df['municipio'] == destino]['br'].unique())
            common_brs = brs_origem.intersection(brs_destino)

            if not common_brs:
                st.warning(f"Não foi encontrada uma conexão direta via BR entre {origem} e {destino} nos dados registrados.")
            else:
                br_rota = list(common_brs)[0]
                
                # Definir intervalo de KM da rota
                kms_o = df[(df['municipio'] == origem) & (df['br'] == br_rota)]['km']
                kms_d = df[(df['municipio'] == destino) & (df['br'] == br_rota)]['km']
                
                # Tratamento para casos onde não há KM registrado para a cidade
                if kms_o.empty or kms_d.empty:
                    st.warning(f"Dados de KM insuficientes para traçar a rota exata entre {origem} e {destino}.")
                else:
                    start_km, end_km = min(kms_o.min(), kms_d.min()), max(kms_o.max(), kms_d.max())
                    
                    dados_rota = df[
                        (df['br'] == br_rota) & 
                        (df['km'] >= start_km) & 
                        (df['km'] <= end_km)
                    ].copy()

                    if dados_rota.empty:
                        st.info("Nenhum acidente registrado neste trecho específico.")
                    else:
                        # Métricas resumidas
                        total = len(dados_rota)
                        h_pico = dados_rota['horario_hora'].mode()[0]
                        tipo = dados_rota['tipo_acidente'].mode()[0]
                        causa = dados_rota['causa_acidente'].mode()[0]
                        
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Total de Acidentes", total)
                        c2.metric("Horário Crítico", f"{h_pico}h")
                        c3.metric("Tipo Predominante", tipo)
                        
                        st.info(f"**Principal Causa no Trecho:** {causa}")

                        # Mapa de Calor/Pontos
                        st.write("**Mapa de Ocorrências e Pontos de Apoio na Rota**")
                        mid_lat = dados_rota['latitude'].mean()
                        mid_lon = dados_rota['longitude'].mean()
                        m = folium.Map(location=[mid_lat, mid_lon], zoom_start=9)
                        
                        # Adicionar Acidentes (Vermelho)
                        for _, row in dados_rota.iterrows():
                            folium.CircleMarker(
                                [row['latitude'], row['longitude']],
                                radius=5, color='red', fill=True, fill_opacity=0.6,
                                popup=f"KM {row['km']} | {row['tipo_acidente']}"
                            ).add_to(m)
                        
                        # Buscar e Adicionar Postos de Gasolina (Azul)
                        lat_min, lat_max = dados_rota['latitude'].min(), dados_rota['latitude'].max()
                        lon_min, lon_max = dados_rota['longitude'].min(), dados_rota['longitude'].max()
                        
                        resultado_postos = buscar_postos_gasolina(lat_min, lon_min, lat_max, lon_max)
                        
                        if isinstance(resultado_postos, list):
                            for posto in resultado_postos:
                                nome_posto = posto.get('tags', {}).get('name', 'Posto de Gasolina')
                                folium.Marker(
                                    [posto['lat'], posto['lon']],
                                    popup=nome_posto,
                                    icon=folium.Icon(color='blue', icon='info-sign')
                                ).add_to(m)
                            
                            if resultado_postos:
                                st.caption(f"ℹ️ Foram encontrados {len(resultado_postos)} postos de gasolina próximos ao trecho.")
                            else:
                                st.caption("ℹ️ Nenhum posto de gasolina mapeado nesta região específica.")
                        else:
                            st.warning(f"⚠️ Não foi possível carregar os postos: {resultado_postos}")
                        
                        # Adicionando uma chave única para evitar problemas de recarregamento
                        st_folium(m, width=1000, height=450, key=f"mapa_{origem}_{destino}")
                        
                        # Gráficos de apoio
                        st.write("---")
                        col_g1, col_g2 = st.columns(2)
                        with col_g1:
                            st.write("**Acidentes por Hora (Rota)**")
                            st.bar_chart(dados_rota['horario_hora'].value_counts().sort_index())
                        with col_g2:
                            st.write("**Top 5 Causas (Rota)**")
                            st.bar_chart(dados_rota['causa_acidente'].value_counts().head(5))

elif modo_analise == "Análise Geral PE":
    st.subheader("Em construção")

elif modo_analise == "Comparativo Anual":
    st.subheader("Em construção")

# Rodapé Informativo
st.sidebar.markdown("---")
st.sidebar.caption(f"Dados processados: {len(df)} registros")
st.sidebar.caption("Versão: 0.8.2-beta (Build 20260515)")
st.sidebar.caption("Fonte: Dados Abertos PRF")
