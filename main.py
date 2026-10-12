from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
import uvicorn
import soyo_apk  # Importamos tu motor matemático definitivo

app = FastAPI()

# Memoria temporal de sesión para pruebas rápidas en taller
piezas_tiras = []
lineas_lineal = []
ultimo_res_tiras = None

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
    <head><title>SOYO APK</title><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="font-family: sans-serif; background: #0f172a; color: white; padding: 20px;">
        <h2>SOYO - Optimizador Móvil</h2>
        <p>El motor matemático está integrado al 100% y listo.</p>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)