import streamlit as st
import pdfplumber
import re
from datetime import datetime
import google.generativeai as genai

# --- FUNÇÃO DE EXTRAÇÃO E EXTRAÇÃO DE METADADOS ---
def processar_e_anonimizar_pdf(arquivo_pdf):
    texto_bruto_completo = ""
    texto_anonimizado_completo = ""
    dados_demograficos = "Não identificado"
    medico_solicitante = "Não identificado"
    
    with pdfplumber.open(arquivo_pdf) as pdf:
        for pagina in pdf.pages:
            t = pagina.extract_text()
            if t:
                texto_bruto_completo += t + "\n"
                
    # --- NOVO MOTOR DE EXTRAÇÃO ANCORADO POR PALAVRA-CHAVE ---
    idade_txt = ""
    sexo_txt = ""
    
    # 1. Tenta o padrão direto clássico "Idade / Sexo: 65a / M"
    match_demog = re.search(r'Idade\s*/\s*Sexo\s*:\s*([^\n]+)', texto_bruto_completo, re.IGNORECASE)
    
    if match_demog:
        dados_demograficos = match_demog.group(1).strip()
    else:
        # Tenta achar idade direta isolada (ex: "Idade: 45")
        match_idade_so = re.search(r'Idade\s*:\s*(\d+)', texto_bruto_completo, re.IGNORECASE)
        if match_idade_so:
            idade_txt = f"{match_idade_so.group(1)}a"
        else:
            # Captura cirúrgica baseada em âncoras de texto
            # Busca a linha do nascimento
            match_nasc_linha = re.search(r'(?:Nasc(?:imento)?|Data\s+of\s+Birth)[\s*:]+(\d{2}/\d{2}/\d{4})', texto_bruto_completo, re.IGNORECASE)
            # Busca a linha da data do exame/ficha
            match_exame_linha = re.search(r'(?:Data\s+da\s+Ficha|Emiss[ãa]o|Cadastro|Data\s+Exame)[\s*:]+(\d{2}/\d{2}/\d{4})', texto_bruto_completo, re.IGNORECASE)
            
            if match_nasc_linha and match_exame_linha:
                try:
                    dt_nasc = datetime.strptime(match_nasc_linha.group(1), "%d/%m/%Y")
                    dt_exame = datetime.strptime(match_exame_linha.group(1), "%d/%m/%Y")
                    
                    calc_idade = dt_exame.year - dt_nasc.year - (
                        (dt_exame.month, dt_exame.day) < (dt_nasc.month, dt_nasc.day)
                    )
                    idade_txt = f"{calc_idade}a"
                except:
                    idade_txt = "Não identificado"
            else:
                # Se a ancoragem falhar, tenta usar as duas primeiras datas genéricas que encontrar na página
                todas_as_datas = re.findall(r'\b\d{2}/\d{2}/\d{4}\b', texto_bruto_completo)
                if len(todas_as_datas) >= 2:
                    try:
                        dt1 = datetime.strptime(todas_as_datas[0], "%d/%m/%Y")
                        dt2 = datetime.strptime(todas_as_datas[1], "%d/%m/%Y")
                        data_nascimento = min(dt1, dt2)
                        data_exame = max(dt1, dt2)
                        calc_idade = data_exame.year - data_nascimento.year - ((data_exame.month, data_exame.day) < (data_nascimento.month, data_nascimento.day))
                        idade_txt = f"{calc_idade}a"
                    except:
                        idade_txt = "Não identificado"
                else:
                    idade_txt = "Não identificado"

        # Tenta achar sexo isolado no texto do cabeçalho
        match_sexo_so = re.search(r'Sexo\s*:\s*\b(M|F|Masculino|Feminino)\b', texto_bruto_completo, re.IGNORECASE)
        if match_sexo_so:
            sexo_txt = "M" if match_sexo_so.group(1).strip().upper().startswith("M") else "F"
        else:
            sexo_txt = "Não informado"

        dados_demograficos = f"{idade_txt} / {sexo_txt}"
        
    # --- CAPTURA DO MÉDICO ---
    match_medico = re.search(r'Médico\s*:\s*([^\n]+)', texto_bruto_completo, re.IGNORECASE)
    if match_medico:
        medico_solicitante = match_medico.group(1).strip()
        medico_solicitante = re.sub(r'\bCRM.*', '', medico_solicitante, flags=re.IGNORECASE).strip()
            
    # --- SEGUNDA PASSAGEM: LIMPEZA E ANONIMIZAÇÃO ---
    with pdfplumber.open(arquivo_pdf) as pdf:
        for i, pagina in enumerate(pdf.pages):
            texto_pagina = pagina.extract_text()
            if texto_pagina:
                linhas = texto_pagina.split('\n')
                linhas_resultados = []
                passou_o_cabecalho = False
                
                marcos_fim_cabecalho = ["resultados de exames", "www.tecnolab", "senha:", "acesso ao laudo", "coleta:"]
                termos_bloqueados = ["registro:", "pedido:", "médico:", "convenio:", "idade / sexo", "data cadastro", "cadastro:", "data nascimento:", "ficha:", "data da ficha:"]
                
                for linha in lines:
                    linha_limpa = linha.strip()
                    if not linha_limpa:
                        continue
                    
                    linha_lower = linha_limpa.lower()
                    if any(marco in linha_lower for marco in marcos_fim_cabecalho):
                        passou_o_cabecalho = True
                        continue
                    
                    if passou_o_cabecalho:
                        if any(termo in linha_lower for termo in termos_bloqueados):
                            continue
                        linhas_resultados.append(linha_limpa)
                
                if not linhas_resultados:
                    for linha in linhas:
                        linha_limpa = linha.strip()
                        if not any(t in linha_limpa.lower() for t in termos_bloqueados):
                            linhas_resultados.append(linha_limpa)
                
                texto_pagina_limpo = "\n".join(linhas_resultados)
                if texto_pagina_limpo.strip():
                    texto_anonimizado_completo += f"--- RESULTADOS DA PÁGINA {i+1} ---\n"
                    texto_anonimizado_completo += texto_pagina_limpo + "\n\n"
                    
    return texto_anonimizado_completo, dados_demograficos, medico_solicitante


def analisar_resultados_com_ia(texto_limpo, dados_demograficos, etnia_afro, medico, chave_api):
    try:
        genai.configure(api_key=chave_api)
        model = genai.GenerativeModel(model_name="gemini-2.5-flash")
        
        prompt = f"""
        Atue como um analista laboratorial avançado emitindo uma nota técnica de suporte ao médico solicitante.
        
        MÉDICO SOLICITANTE: {medico}
        
        PERFIL FISIOLÓGICO DO PACIENTE (ANÔNIMO):
        - Idade e Sexo Biológico Estabilizados: {dados_demograficos}
        - O usuário confirmou que o paciente possui ancestralidade/etnia afrodescendente? Resposta: {etnia_afro}. (Utilize esta informação estritamente se houver cálculo de eGFR/função renal no texto).
        
        Sua tarefa:
        1. Direcione formalmente o início do parecer ao(à) {medico}.
        2. Agrupe os exames por categorias lógicas.
        3. Identifique e destaque claramente quais resultados estão FORA dos valores de referência laboratoriais esperados para este perfil fisiológico.
        4. Redija um parecer clínico conciso, estruturado e objetivo, facilitando a tomada de decisão médica.
        
        RESTRIÇÃO CRÍTICA DE ENCERRAMENTO:
        Termine o texto imediatamente após a conclusão da análise técnica. Não adicione nenhuma frase de cortesia, encerramento formal, saudações finais ou mensagens corporativas como "Colocamo-nos à disposição para quaisquer esclarecimentos adicionais" ou similares.
        
        DADOS DOS EXAMES:
        {texto_limpo}
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Erro na análise da IA: {e}. Verifique se a sua chave API é válida."


# --- INTERFACE (STREAMLIT) ---
st.set_page_config(page_title="Analisador Clínico IA", layout="centered")

st.sidebar.header("⚙️ Configuração")
api_key_input = st.sidebar.text_input("Google Gemini API Key", type="password", help="A sua chave não é guardada nos servidores.")

st.title("🔬 Analisador de Exames com IA")

st.subheader("📋 Informações Clínicas Adicionais")

col_etnia, col_sexo = st.columns(2)

with col_etnia:
    etnia_selecionada = st.radio(
        "Paciente é de etnia afrodescendente?",
        ["Não", "Sim", "Não informado"]
    )

with col_sexo:
    sexo_selecionado = st.radio(
        "Sexo biológico do paciente:",
        ["Usar dados do laudo", "Masculino", "Feminino"]
    )

st.write("---")
arquivo_upado = st.file_uploader("Carregue o PDF do laudo", type=["pdf"])

if arquivo_upado is not None:
    if st.button("Processar e Analisar Exames"):
        if not api_key_input:
            st.error("Por favor, insira a sua Gemini API Key na barra lateral esquerda para prosseguir.")
        else:
            with st.spinner("Higienizando laudo e extraindo variáveis..."):
                texto_anonimizado, demograficos, medico = processar_e_anonimizar_pdf(arquivo_upado)
                
                if not texto_anonimizado.strip():
                    st.error("Não foi possível isolar os exames do laudo.")
                else:
                    st.success("Dados do laudo processados com sucesso!")
                    
                    # --- RECONCILIAÇÃO DEMOGRÁFICA ---
                    idade_exibicao = "Não identificado"
                    sexo_exibicao = "Não informado"
                    
                    if "/" in demograficos:
                        partes = demograficos.split("/")
                        idade_exibicao = partes[0].strip()
                        sexo_exibicao = partes[1].strip()
                    else:
                        if "a" in demograficos or any(c.isdigit() for c in demograficos):
                            idade_exibicao = demograficos
                    
                    # Substituição manual via botões da Interface
                    if sexo_selecionado != "Usar dados do laudo":
                        sexo_exibicao = "M" if sexo_selecionado == "Masculino" else "F"
                    
                    demograficos_finais = f"{idade_exibicao} / {sexo_exibicao}"
                    
                    # 1. Injeção de CSS Global para customizar as colunas
                    st.markdown("""
                        <style>
                        [data-testid="stColumn"] {
                            border: 1px solid #4A4A4A !important;
                            padding: 12px !important;
                            border-radius: 5px !important;
                            background-color: rgba(255, 255, 255, 0.02) !important;
                        }
                        [data-testid="stColumn"] p {
                            margin-bottom: 2px !important;
                        }
                        </style>
                    """, unsafe_allow_html=True)
                    
                    # 2. Renderização usando componentes nativos organizados
                    col1, col2, col3 = st.columns([2, 1, 1])
                    
                    with col1:
                        st.caption("Médico Solicitante")
                        st.markdown(f"**{medico}**")
                        
                    with col2:
                        st.caption("Idade / Sexo")
                        st.markdown(f"**{demograficos_finais}**")
                        
                    with col3:
                        st.caption("Afrodescendente")
                        st.markdown(f"**{etnia_selecionada}**")
                    
                    st.write("") 
                    
                    with st.spinner("O Gemini está gerando o parecer clínico preliminar..."):
                        parecer_final = analisar_resultados_com_ia(
                            texto_anonimizado, demograficos_finais, etnia_selecionada, medico, api_key_input
                        )
                    
                    st.subheader("📋 Parecer Destinado ao Médico")
                    st.markdown(parecer_final)
