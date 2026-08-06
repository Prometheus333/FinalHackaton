import os
import httpx
import requests
import urllib3
import warnings
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Importaciones de Alpaca y LangChain
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from langchain.agents import create_tool_calling_agent, AgentExecutor

# ==========================================
# 0. CARGAR VARIABLES DE ENTORNO (.env)
# ==========================================
load_dotenv()

# ==========================================
# 1. PARCHES DE RED PARA EL ENTORNO CORPORATIVO
# ==========================================
os.environ["OTEL_SDK_DISABLED"] = "true"
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", module="urllib3")

client_llm = httpx.Client(verify=False)
original_request = requests.Session.request

def patched_request(self, method, url, **kwargs):
    kwargs['verify'] = False
    return original_request(self, method, url, **kwargs)
requests.Session.request = patched_request

# ==========================================
# 2. CONFIGURACIÓN DE FASTAPI Y LLM
# ==========================================
app = FastAPI(title="Capital Markets AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cargar credenciales desde el .env
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
    raise ValueError("Faltan las credenciales de Alpaca en el archivo .env")

data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

# Configurar LLM usando las llaves del .env
llm = ChatOpenAI(
    base_url=os.getenv("GENAI_BASE_URL", "https://genailab.tcs.in"),
    model="azure/genailab-maas-gpt-4.1",
    api_key=os.getenv("GENAI_API_KEY"),
    http_client=client_llm,
    temperature=0.2
)

# ==========================================
# 3. HERRAMIENTAS Y AGENTES
# ==========================================
@tool
def obtener_datos_ohlcv(simbolo: str) -> str:
    """Extrae el historial OHLCV de los últimos 30 días de una acción."""
    try:
        fecha_fin = datetime.now()
        fecha_inicio = fecha_fin - timedelta(days=30)
        request_params = StockBarsRequest(
            symbol_or_symbols=simbolo, timeframe=TimeFrame.Day,
            start=fecha_inicio, end=fecha_fin
        )
        bars = data_client.get_stock_bars(request_params)
        return bars.df.tail(10).to_string()
    except Exception as e:
        return f"Error: {str(e)}"

herramientas = [obtener_datos_ohlcv]

prompt_datos = ChatPromptTemplate.from_messages([
    ("system", "Eres un ingeniero de datos. Extrae datos crudos (OHLCV) y entrégalos estructurados."),
    ("user", "{input}"),
    ("placeholder", "{agent_scratchpad}") 
])
prompt_riesgos = ChatPromptTemplate.from_messages([
    ("system", "Eres un analista cuantitativo. Responde con: 1) Nivel de Riesgo (BAJO/MEDIO/ALTO), 2) Una frase sobre la volatilidad, 3) Un nivel de soporte clave."),
    ("user", "Analiza estos datos:\n\n{datos_mercado}")
])
# Prompt del Agente Estratega (Resultado accional)
prompt_estratega = ChatPromptTemplate.from_messages([
    ("system", "Eres un trader senior. Responde estrictamente con: 1) ACCIÓN (COMPRAR/VENDER/MANTENER), 2) PRECIO OBJETIVO, 3) Justificación técnica en una sola línea."),
    ("user", "DATOS:\n{datos_mercado}\n\nRIESGO:\n{reporte_riesgo}")
])

agente_extractor = create_tool_calling_agent(llm, herramientas, prompt_datos)
extractor_executor = AgentExecutor(agent=agente_extractor, tools=herramientas, verbose=False)
cadena_riesgos = prompt_riesgos | llm
cadena_estratega = prompt_estratega | llm

# ==========================================
# 4. ENDPOINTS DE LA API
# ==========================================
class AnalyzeRequest(BaseModel):
    ticker: str

@app.post("/api/analyze")
async def analyze_strategy(req: AnalyzeRequest):
    try:
        # 1. Obtener datos numéricos para el gráfico de la UI
        fecha_fin = datetime.now()
        fecha_inicio = fecha_fin - timedelta(days=30)
        bars = data_client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=req.ticker.upper(), timeframe=TimeFrame.Day,
            start=fecha_inicio, end=fecha_fin
        ))
        
        # Formatear datos para el gráfico (fechas y precios de cierre)
        df = bars.df.reset_index()
        chart_data = [
            {"date": row["timestamp"].strftime("%Y-%m-%d"), "close": row["close"], "volume": row["volume"]}
            for _, row in df.iterrows()
        ]

        # 2. Ejecución de los Agentes
        res_extraccion = extractor_executor.invoke({"input": f"Extrae los últimos 30 días para {req.ticker}."})
        datos = res_extraccion["output"]

        res_riesgo = cadena_riesgos.invoke({"datos_mercado": datos})
        riesgo = res_riesgo.content

        res_estratega = cadena_estratega.invoke({"datos_mercado": datos, "reporte_riesgo": riesgo})
        estrategia = res_estratega.content

        return {
            "ticker": req.ticker.upper(),
            "data_text": datos,
            "chart_data": chart_data, # <--- ¡Nuevos datos para graficar!
            "risk_report": riesgo,
            "strategy": estrategia
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))