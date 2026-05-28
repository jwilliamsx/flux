import streamlit as st
import pandas as pd
import sqlite3
import os
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium

# --- 1. FUNÇÕES DE BANCO DE DADOS ---

def get_connection():
    """Retorna uma conexão com o banco de dados SQLite."""
    os.makedirs("data", exist_ok=True)
    return sqlite3.connect("data/acidentes.db", check_same_thread=False)

def criar_banco_se_nao_existir(force_update=False):
    """
    Carrega os CSVs, padroniza, combina e insere no SQLite com índices.
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
            
            # Garantir que feridos e mortos são numéricos
            for col in ['mortos', 'feridos_graves', 'feridos_leves', 'feridos']:
                if col in df_temp.columns:
                    df_temp[col] = pd.to_numeric(df_temp[col], errors='coerce').fillna(0)
            
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
        
        # Criar índices para performance
        status_text.text("Otimizando consultas (criando índices)...")
        cursor = conn.cursor()
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_uf_ano ON acidentes(uf, ano)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_br_km ON acidentes(br, km)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_municipio ON acidentes(municipio)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_causa ON acidentes(causa_acidente)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tipo ON acidentes(tipo_acidente)")
        conn.commit()
        conn.close()
        
        status_text.text("Banco de dados pronto e otimizado!")
        progress_bar.empty()
        st.sidebar.success("Dados carregados e otimizados com sucesso!")
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
        
        # Formatação de colunas de texto para melhor visualização
        for col in ['causa_acidente', 'tipo_acidente', 'classificacao_acidente', 'condicao_metereologica', 'fase_dia']:
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace('_', ' ').str.title().replace('Nan', pd.NA)
            
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
    anos_disponiveis = sorted(consultar_dados("SELECT DISTINCT ano FROM acidentes")['ano'].dropna().tolist())
except:
    anos_disponiveis = []

if not anos_disponiveis:
    st.warning("Nenhum dado disponível. Verifique os arquivos CSV e clique em Atualizar.")
    st.stop()

filtro_ano = st.sidebar.multiselect("Selecione os Anos", options=anos_disponiveis, default=anos_disponiveis)

# Filtros Avançados
st.sidebar.subheader("🎯 Filtros Avançados")
todas_causas = sorted(consultar_dados("SELECT DISTINCT causa_acidente FROM acidentes")['causa_acidente'].dropna().unique())
filtro_causa = st.sidebar.multiselect("Causa do Acidente", options=todas_causas)

todos_tipos = sorted(consultar_dados("SELECT DISTINCT tipo_acidente FROM acidentes")['tipo_acidente'].dropna().unique())
filtro_tipo = st.sidebar.multiselect("Tipo de Acidente", options=todos_tipos)

todas_classes = sorted(consultar_dados("SELECT DISTINCT classificacao_acidente FROM acidentes")['classificacao_acidente'].dropna().unique())
filtro_classe = st.sidebar.multiselect("Classificação", options=todas_classes)

# Query dinâmica baseada nos anos e filtros selecionados
query_base = f"SELECT * FROM acidentes WHERE uf = 'PE' AND ano IN ({','.join(map(str, filtro_ano))})"
df = consultar_dados(query_base)

# Aplicar filtros avançados no DataFrame em memória (mais rápido que nova query SQL complexa para multiselects vazios)
if filtro_causa:
    df = df[df['causa_acidente'].isin(filtro_causa)]
if filtro_tipo:
    df = df[df['tipo_acidente'].isin(filtro_tipo)]
if filtro_classe:
    df = df[df['classificacao_acidente'].isin(filtro_classe)]

if df.empty:
    st.info("Nenhum dado encontrado para os filtros selecionados.")
    st.stop()

st.title("🚗 Flux - Análise de Acidentes")

menu = ["Análise de Rota", "Análise Geral PE", "Comparativo Anual"]
modo_analise = st.sidebar.radio("Selecione o Modo de Análise", menu)

# --- 3. MODOS DE ANÁLISE ---

if modo_analise == "Análise de Rota":
    st.subheader("📍 Análise de Pontos Críticos por Rota")
    
    cidades_pe = sorted(df['municipio'].dropna().unique())
    
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
                        
                        # Adicionando uma chave única para evitar problemas de recarregamento
                        st_folium(m, width=1000, height=450, key=f"mapa_{origem}_{destino}")
                        
                        # Cálculo de Gravidade e Pontos Negros
                        dados_rota['gravidade'] = (dados_rota['mortos'] * 10) + \
                                                  (dados_rota['feridos_graves'] * 5) + \
                                                  (dados_rota['feridos_leves'] * 2)
                        
                        st.write("---")
                        col_stats, col_perigo = st.columns([1, 1])
                        
                        with col_stats:
                            st.write("**🔥 Trechos Mais Críticos (Pontos Negros)**")
                            # Agrupar por KM arredondado para identificar áreas de concentração
                            pontos_negros = dados_rota.groupby('km').agg({
                                'id': 'count',
                                'gravidade': 'sum'
                            }).rename(columns={'id': 'Qtd Acidentes', 'gravidade': 'Gravidade Total'}).sort_values('Gravidade Total', ascending=False).head(5)
                            st.table(pontos_negros)

                        with col_perigo:
                            st.write("**⚠️ Fatores de Risco no Trecho**")
                            pior_clima = dados_rota['condicao_metereologica'].mode()[0]
                            pior_fase = dados_rota['fase_dia'].mode()[0]
                            st.warning(f"**Clima Predominante em Acidentes:** {pior_clima}")
                            st.warning(f"**Fase do Dia Mais Perigosa:** {pior_fase}")

                        # Gráficos de apoio
                        st.write("---")
                        col_g1, col_g2 = st.columns(2)
                        with col_g1:
                            st.write("**Acidentes por Hora (Rota)**")
                            st.bar_chart(dados_rota['horario_hora'].value_counts().sort_index().rename_axis("Hora").rename("Qtd Acidentes"))
                        with col_g2:
                            st.write("**Top 5 Causas (Rota)**")
                            st.bar_chart(dados_rota['causa_acidente'].value_counts().head(5).rename_axis("Causa").rename("Qtd Acidentes"))

elif modo_analise == "Análise Geral PE":
    st.subheader("📊 Análise Geral - Pernambuco")
    
    # 1. Métricas Principais (KPIs)
    total_acidentes = len(df)
    total_mortos = df['mortos'].sum()
    total_feridos = df['feridos'].sum()
    total_veiculos = df['veiculos'].sum()
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total de Acidentes", total_acidentes)
    col2.metric("Total de Mortos", total_mortos, delta=None, delta_color="inverse")
    col3.metric("Total de Feridos", total_feridos)
    col4.metric("Veículos Envolvidos", total_veiculos)
    
    st.write("---")
    
    # 2. Distribuição Temporal e Espacial
    col_temp, col_city = st.columns(2)
    
    with col_temp:
        st.write("**Acidentes por Hora do Dia**")
        hist_hora = df['horario_hora'].value_counts().sort_index().rename_axis("Hora").rename("Qtd Acidentes")
        st.bar_chart(hist_hora)
        
        st.write("**Acidentes por Dia da Semana**")
        # Ordenando dias da semana logicamente
        dias_ordem = ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira', 'Quinta-Feira', 'Sexta-Feira', 'Sábado', 'Domingo']
        hist_semana = df['dia_semana'].value_counts().reindex(dias_ordem).rename_axis("Dia da Semana").rename("Qtd Acidentes")
        st.bar_chart(hist_semana)

    with col_city:
        st.write("**Top 10 Municípios com Mais Acidentes**")
        top_cidades = df['municipio'].value_counts().head(10).rename_axis("Município").rename("Qtd Acidentes")
        st.bar_chart(top_cidades)
        
        st.write("**Classificação dos Acidentes**")
        classificacao = df['classificacao_acidente'].value_counts().rename_axis("Classificação").rename("Qtd Acidentes")
        st.bar_chart(classificacao)

    st.write("---")
    
    # 3. Causas e Tipos
    col_causa, col_tipo = st.columns(2)
    
    with col_causa:
        st.write("**Top 10 Causas de Acidentes**")
        top_causas = df['causa_acidente'].value_counts().head(10).rename_axis("Causa").rename("Qtd Acidentes")
        st.bar_chart(top_causas)
        
    with col_tipo:
        st.write("**Top 10 Tipos de Acidentes**")
        top_tipos = df['tipo_acidente'].value_counts().head(10).rename_axis("Tipo").rename("Qtd Acidentes")
        st.bar_chart(top_tipos)

    # 4. Mapa de Calor do Estado
    st.write("---")
    st.write("**Mapa de Calor de Acidentes (PE)**")
    
    # Amostragem para performance
    df_mapa = df.sample(min(3000, len(df)))
    
    m_geral = folium.Map(location=[-8.3, -37.8], zoom_start=7)
    
    # Preparar dados para o HeatMap: [[lat, lon, peso], ...]
    heat_data = [[row['latitude'], row['longitude']] for _, row in df_mapa.iterrows()]
    
    HeatMap(heat_data, radius=10, blur=15, min_opacity=0.5).add_to(m_geral)
        
    st_folium(m_geral, width=1000, height=500, key="mapa_geral_pe")

elif modo_analise == "Comparativo Anual":
    st.subheader("📈 Comparativo Anual")
    
    if len(filtro_ano) < 2:
        st.warning("Selecione pelo menos dois anos no menu lateral para comparar.")
    else:
        # Agrupar dados por ano
        comparativo = df.groupby('ano').agg({
            'id': 'count',
            'mortos': 'sum',
            'feridos': 'sum',
            'veiculos': 'sum'
        }).rename(columns={
            'id': 'Total Acidentes',
            'mortos': 'Mortos',
            'feridos': 'Feridos',
            'veiculos': 'Veículos'
        }).rename_axis("Ano")
        
        st.write("**Estatísticas Comparativas**")
        st.dataframe(comparativo.style.highlight_max(axis=0, color='lightcoral'))
        
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            st.write("**Evolução de Acidentes**")
            st.line_chart(comparativo['Total Acidentes'].rename("Contagem de Acidentes"))
        with col_c2:
            st.write("**Evolução de Óbitos**")
            st.line_chart(comparativo['Mortos'].rename("Contagem de Óbitos"))
        
        st.info("💡 A análise comparativa ajuda a identificar tendências de aumento ou redução na segurança viária ao longo dos anos.")

# Rodapé Informativo
st.sidebar.markdown("---")
st.sidebar.caption(f"Dados processados: {len(df)} registros")
st.sidebar.caption("Versão: 0.8.2-beta (Build 20260515)")
st.sidebar.caption("Fonte: Dados Abertos PRF")
