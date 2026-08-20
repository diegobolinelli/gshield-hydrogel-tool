"""
GShield - Gerador de imagem Hydrogel + foto do celular

Fluxo:

1) /api/buscar?modelo=...
   - procura a página do modelo no GSMArena
   - usa Bing Web Search API quando BING_API_KEY estiver configurada
   - usa DuckDuckGo como fallback
   - mantém cache das URLs encontradas para reduzir consultas
   - baixa a foto principal do aparelho

2) /api/compor
   - recebe uma imagem do celular
   - compõe com o template do Hydrogel
   - retorna JPG

IMPORTANTE:
- A busca automática é best-effort.
- Sites de terceiros podem mudar sua estrutura ou limitar automação.
- O modo manual continua sendo o fallback mais confiável.
"""

import base64
import io
import os
import re
import time
from urllib.parse import quote, unquote, urlparse

import requests
from flask import Flask, jsonify, request, send_file, send_from_directory
from PIL import Image


app = Flask(__name__, static_folder="static", static_url_path="")


# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

TEMPLATE_PATH = "static/template.jpg"

CANVAS_SIZE = (2000, 2000)

PHONE_BOX = (70, 220, 900, 1780)


# ============================================================================
# HEADERS
# ============================================================================

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


SEARCH_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


GSMARENA_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://www.google.com/",
}


# ============================================================================
# CONFIGURAÇÃO DO BING
# ============================================================================

# Configure na Vercel:
#
# BING_API_KEY=xxxxxxxxxxxxxxxx
#
BING_API_KEY = os.environ.get("BING_API_KEY", "").strip()

BING_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"


# ============================================================================
# REGEX
# ============================================================================

GSMARENA_LINK_RE = re.compile(
    r"https?://(?:www\.)?gsmarena\.com/[a-zA-Z0-9_]+-\d+\.php",
    re.IGNORECASE,
)


GSMARENA_PHOTO_RE = re.compile(
    r"specs-photo-main.*?<img[^>]+src=[\"']?([^\"'>\s]+)",
    re.IGNORECASE | re.DOTALL,
)


OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


DUCKDUCKGO_UDDG_RE = re.compile(
    r"uddg=([^&\"']+)"
)


GSMARENA_EXCLUIR = (
    "-pictures-",
    "-review",
    "compare.php3",
    "-news-",
    "-vs-",
    "glossary",
)


# ============================================================================
# CACHE
# ============================================================================
#
# A Vercel Function pode reutilizar a mesma instância por algum tempo.
# Esse cache não é garantia de persistência, mas já reduz bastante chamadas
# repetidas durante o uso normal.
#
# Para persistência real entre instâncias, futuramente vale usar Supabase,
# Redis ou outro storage externo.
#

URL_CACHE = {}

CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 dias


def normalizar_modelo(modelo: str) -> str:
    """
    Normaliza o nome usado como chave de cache.
    """

    modelo = modelo.strip().lower()

    modelo = re.sub(r"\s+", " ", modelo)

    return modelo


def cache_get(modelo: str):
    chave = normalizar_modelo(modelo)

    item = URL_CACHE.get(chave)

    if not item:
        return None

    timestamp, url = item

    if time.time() - timestamp > CACHE_TTL_SECONDS:
        URL_CACHE.pop(chave, None)
        return None

    return url


def cache_set(modelo: str, url: str):
    chave = normalizar_modelo(modelo)

    URL_CACHE[chave] = (
        time.time(),
        url,
    )


# ============================================================================
# VALIDAÇÃO GSMARENA
# ============================================================================

def eh_url_gsmarena_valida(url: str) -> bool:
    """
    Confirma que a URL encontrada pertence ao GSMArena
    e tem formato de página de aparelho.
    """

    if not url:
        return False

    url = url.strip()

    if not url.startswith(("http://", "https://")):
        return False

    try:
        parsed = urlparse(url)

        hostname = (parsed.hostname or "").lower()

        if hostname not in {
            "gsmarena.com",
            "www.gsmarena.com",
        }:
            return False

    except Exception:
        return False

    if not GSMARENA_LINK_RE.search(url):
        return False

    if any(
        trecho in url.lower()
        for trecho in GSMARENA_EXCLUIR
    ):
        return False

    return True


def extrair_link_gsmarena(texto: str):
    """
    Procura uma URL válida do GSMArena em um texto qualquer.
    """

    if not texto:
        return None

    encontrados = GSMARENA_LINK_RE.findall(texto)

    for candidato in encontrados:

        if eh_url_gsmarena_valida(candidato):
            return candidato

    return None


# ============================================================================
# BUSCA VIA BING API
# ============================================================================

def buscar_url_bing(modelo: str):
    """
    Busca a página do modelo usando Bing Web Search API.

    Requer:
        BING_API_KEY

    A API oficial do Bing aceita consultas com o operador site:.
    """

    if not BING_API_KEY:
        return None

    query = f"site:gsmarena.com {modelo} specifications"

    headers = {
        "Ocp-Apim-Subscription-Key": BING_API_KEY,
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }

    params = {
        "q": query,
        "count": 10,
        "responseFilter": "Webpages",
        "textDecorations": "false",
        "textFormat": "Raw",
    }

    try:

        response = requests.get(
            BING_ENDPOINT,
            headers=headers,
            params=params,
            timeout=15,
        )

        if response.status_code != 200:
            return None

        data = response.json()

        webpages = data.get("webPages", {})

        values = webpages.get("value", [])

        for result in values:

            url = result.get("url")

            if eh_url_gsmarena_valida(url):
                return url

        return None

    except (
        requests.exceptions.RequestException,
        ValueError,
        TypeError,
    ):
        return None


# ============================================================================
# BUSCA VIA DUCKDUCKGO
# ============================================================================

def buscar_url_duckduckgo(modelo: str):
    """
    Fallback usando a versão HTML do DuckDuckGo.

    IMPORTANTE:
    Se o DDG retornar 202/anomaly, consideramos a busca bloqueada.
    Não tentamos contornar o bloqueio.
    """

    query = f"site:gsmarena.com {modelo} specifications"

    url = (
        "https://html.duckduckgo.com/html/"
        f"?q={quote(query)}"
    )

    try:

        response = requests.get(
            url,
            headers=SEARCH_HEADERS,
            timeout=15,
        )

    except requests.exceptions.RequestException:
        return None

    # ------------------------------------------------------------------------
    # NÃO tratar 202 como resposta normal.
    # ------------------------------------------------------------------------

    if response.status_code != 200:
        return None

    html = response.text

    html_lower = html.lower()

    # ------------------------------------------------------------------------
    # Detecta páginas de bloqueio/anomalia.
    # ------------------------------------------------------------------------

    sinais_bloqueio = (
        "anomaly",
        "unusual traffic",
        "automated",
        "captcha",
        "too many requests",
        "verify you are human",
    )

    if any(
        sinal in html_lower
        for sinal in sinais_bloqueio
    ):
        return None

    # ------------------------------------------------------------------------
    # 1. Link direto
    # ------------------------------------------------------------------------

    encontrado = extrair_link_gsmarena(html)

    if encontrado:
        return encontrado

    # ------------------------------------------------------------------------
    # 2. Links embrulhados em uddg
    # ------------------------------------------------------------------------

    for wrapped in DUCKDUCKGO_UDDG_RE.findall(html):

        try:
            decoded = unquote(wrapped)

        except Exception:
            continue

        encontrado = extrair_link_gsmarena(decoded)

        if encontrado:
            return encontrado

    return None


# ============================================================================
# BUSCA PRINCIPAL
# ============================================================================

def buscar_url_produto(modelo: str):
    """
    Estratégia:

    1. Cache
    2. Bing API
    3. DuckDuckGo fallback
    4. None
    """

    # ------------------------------------------------------------------------
    # CACHE
    # ------------------------------------------------------------------------

    cached = cache_get(modelo)

    if cached and eh_url_gsmarena_valida(cached):

        return cached

    # ------------------------------------------------------------------------
    # BING
    # ------------------------------------------------------------------------

    url = buscar_url_bing(modelo)

    if url:

        cache_set(modelo, url)

        return url

    # ------------------------------------------------------------------------
    # DUCKDUCKGO FALLBACK
    # ------------------------------------------------------------------------

    url = buscar_url_duckduckgo(modelo)

    if url:

        cache_set(modelo, url)

        return url

    return None


# ============================================================================
# DOWNLOAD DA IMAGEM DO GSMARENA
# ============================================================================

def baixar_imagem_produto(url_produto: str):
    """
    Abre a página do aparelho e tenta encontrar a foto principal.
    """

    if not eh_url_gsmarena_valida(url_produto):
        return None

    try:

        response = requests.get(
            url_produto,
            headers=GSMARENA_HEADERS,
            timeout=15,
        )

        response.raise_for_status()

        html = response.text

    except requests.exceptions.RequestException:

        return None

    # ------------------------------------------------------------------------
    # Primeira tentativa:
    # foto dentro de specs-photo-main
    # ------------------------------------------------------------------------

    match = GSMARENA_PHOTO_RE.search(html)

    img_url = None

    if match:

        img_url = match.group(1)

    # ------------------------------------------------------------------------
    # Segunda tentativa:
    # og:image
    # ------------------------------------------------------------------------

    if not img_url:

        og_match = OG_IMAGE_RE.search(html)

        if og_match:

            img_url = og_match.group(1)

    if not img_url:
        return None

    # ------------------------------------------------------------------------
    # Normaliza URL
    # ------------------------------------------------------------------------

    if img_url.startswith("//"):

        img_url = "https:" + img_url

    elif img_url.startswith("/"):

        img_url = "https://www.gsmarena.com" + img_url

    # ------------------------------------------------------------------------
    # Download da imagem
    # ------------------------------------------------------------------------

    try:

        image_response = requests.get(
            img_url,
            headers=GSMARENA_HEADERS,
            timeout=15,
        )

        image_response.raise_for_status()

        content_type = (
            image_response.headers
            .get("Content-Type", "")
            .lower()
        )

        # Evita aceitar HTML como se fosse imagem.

        if not content_type.startswith("image/"):

            return None

        if not image_response.content:

            return None

        return image_response.content

    except requests.exceptions.RequestException:

        return None


# ============================================================================
# COMPOSIÇÃO
# ============================================================================

def compose_image(phone_bytes: bytes) -> Image.Image:

    template = Image.open(
        TEMPLATE_PATH
    ).convert("RGB")

    if template.size != CANVAS_SIZE:

        template = template.resize(
            CANVAS_SIZE
        )

    phone = Image.open(
        io.BytesIO(phone_bytes)
    )

    if "A" in phone.getbands():

        phone = phone.convert("RGBA")

    else:

        phone = phone.convert("RGB")

    x0, y0, x1, y1 = PHONE_BOX

    box_w = x1 - x0
    box_h = y1 - y0

    # ------------------------------------------------------------------------
    # contain
    # ------------------------------------------------------------------------

    scale = min(
        box_w / phone.width,
        box_h / phone.height,
    )

    new_w = max(
        1,
        int(phone.width * scale),
    )

    new_h = max(
        1,
        int(phone.height * scale),
    )

    phone_resized = phone.resize(
        (new_w, new_h),
        Image.LANCZOS,
    )

    paste_x = (
        x0
        + (box_w - new_w) // 2
    )

    paste_y = (
        y0
        + (box_h - new_h) // 2
    )

    if phone_resized.mode == "RGBA":

        template.paste(
            phone_resized,
            (paste_x, paste_y),
            phone_resized,
        )

    else:

        template.paste(
            phone_resized,
            (paste_x, paste_y),
        )

    return template


# ============================================================================
# API - BUSCAR
# ============================================================================

@app.get("/api/buscar")
def api_buscar():

    modelo = (
        request.args.get("modelo") or ""
    ).strip()

    if not modelo:

        return jsonify(
            ok=False,
            motivo="Digite o nome do modelo.",
        ), 400

    # ------------------------------------------------------------------------
    # Busca URL
    # ------------------------------------------------------------------------

    try:

        url_produto = buscar_url_produto(
            modelo
        )

    except Exception as exc:

        return jsonify(
            ok=False,
            motivo=(
                "Erro interno durante a busca."
            ),
            detalhe=exc.__class__.__name__,
        ), 500

    if not url_produto:

        return jsonify(
            ok=False,
            motivo=(
                "Não encontrei automaticamente "
                "a página desse modelo. "
                "Use a busca manual."
            ),
        )

    # ------------------------------------------------------------------------
    # Baixa imagem
    # ------------------------------------------------------------------------

    imagem_bytes = baixar_imagem_produto(
        url_produto
    )

    if not imagem_bytes:

        return jsonify(
            ok=False,
            motivo=(
                "Encontrei a página do modelo, "
                "mas não consegui obter a imagem principal. "
                "Use a busca manual."
            ),
            url_produto=url_produto,
        )

    # ------------------------------------------------------------------------
    # Base64
    # ------------------------------------------------------------------------

    imagem_b64 = base64.b64encode(
        imagem_bytes
    ).decode("ascii")

    return jsonify(
        ok=True,
        imagem_base64=imagem_b64,
        url_produto=url_produto,
        modelo=modelo,
    )


# ============================================================================
# API - COMPOR
# ============================================================================

@app.post("/api/compor")
def api_compor():

    phone_bytes = None

    # ------------------------------------------------------------------------
    # Upload manual
    # ------------------------------------------------------------------------

    if "foto" in request.files:

        phone_bytes = (
            request.files["foto"].read()
        )

    # ------------------------------------------------------------------------
    # Busca automática
    # ------------------------------------------------------------------------

    elif request.is_json:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        b64 = data.get(
            "imagem_base64"
        )

        if b64:

            try:

                phone_bytes = (
                    base64.b64decode(b64)
                )

            except Exception:

                return jsonify(
                    ok=False,
                    motivo="Imagem base64 inválida.",
                ), 400

    if not phone_bytes:

        return jsonify(
            ok=False,
            motivo=(
                "Nenhuma imagem de celular recebida."
            ),
        ), 400

    # ------------------------------------------------------------------------
    # Composição
    # ------------------------------------------------------------------------

    try:

        resultado = compose_image(
            phone_bytes
        )

    except Exception as exc:

        return jsonify(
            ok=False,
            motivo=(
                f"Não consegui compor a imagem: {exc}"
            ),
        ), 400

    # ------------------------------------------------------------------------
    # JPG
    # ------------------------------------------------------------------------

    buffer = io.BytesIO()

    resultado.save(
        buffer,
        format="JPEG",
        quality=92,
        optimize=True,
    )

    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="image/jpeg",
        download_name="gshield-hydrogel.jpg",
    )


# ============================================================================
# DEBUG - STATUS DA BUSCA
# ============================================================================

@app.get("/api/debug-search")
def api_debug_search():

    modelo = (
        request.args.get("modelo")
        or "iPhone 16 Pro Max"
    ).strip()

    cache_url = cache_get(modelo)

    bing_configurado = bool(
        BING_API_KEY
    )

    return jsonify(
        ok=True,
        modelo=modelo,
        bing_api_configurada=bing_configurado,
        cache_encontrado=bool(cache_url),
        cache_url=cache_url,
        cache_itens=len(URL_CACHE),
    )


# ============================================================================
# DEBUG - BUSCA COMPLETA
# ============================================================================

@app.get("/api/debug-search-full")
def api_debug_search_full():

    modelo = (
        request.args.get("modelo")
        or "iPhone 16 Pro Max"
    ).strip()

    resultado = {
        "ok": True,
        "modelo": modelo,
        "bing_api_configurada": bool(BING_API_KEY),
        "cache": None,
        "bing": None,
        "duckduckgo": None,
        "imagem": None,
    }

    # ------------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------------

    cache_url = cache_get(modelo)

    resultado["cache"] = {
        "encontrado": bool(cache_url),
        "url": cache_url,
    }

    # ------------------------------------------------------------------------
    # Bing
    # ------------------------------------------------------------------------

    inicio = time.time()

    try:

        bing_url = buscar_url_bing(modelo)

        resultado["bing"] = {
            "sucesso": bool(bing_url),
            "url": bing_url,
            "tempo_ms": round(
                (time.time() - inicio) * 1000
            ),
        }

    except Exception as exc:

        resultado["bing"] = {
            "sucesso": False,
            "erro": exc.__class__.__name__,
        }

    # ------------------------------------------------------------------------
    # DuckDuckGo
    # ------------------------------------------------------------------------

    inicio = time.time()

    try:

        ddg_url = buscar_url_duckduckgo(
            modelo
        )

        resultado["duckduckgo"] = {
            "sucesso": bool(ddg_url),
            "url": ddg_url,
            "tempo_ms": round(
                (time.time() - inicio) * 1000
            ),
        }

    except Exception as exc:

        resultado["duckduckgo"] = {
            "sucesso": False,
            "erro": exc.__class__.__name__,
        }

    # ------------------------------------------------------------------------
    # Imagem
    # ------------------------------------------------------------------------

    url_teste = (
        resultado["bing"].get("url")
        if resultado["bing"]
        and resultado["bing"].get("url")
        else resultado["duckduckgo"].get("url")
        if resultado["duckduckgo"]
        else None
    )

    if url_teste:

        inicio = time.time()

        imagem = baixar_imagem_produto(
            url_teste
        )

        resultado["imagem"] = {
            "sucesso": bool(imagem),
            "bytes": len(imagem)
            if imagem
            else 0,
            "tempo_ms": round(
                (time.time() - inicio) * 1000
            ),
        }

    return jsonify(resultado)


# ============================================================================
# FRONTEND
# ============================================================================

@app.get("/")
def index():

    return send_from_directory(
        "static",
        "index.html",
    )


# ============================================================================
# LOCAL
# ============================================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
    )
