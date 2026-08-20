"""
GShield - Gerador de imagem Hydrogel + foto do celular

Fluxo:

1) /api/buscar?modelo=...
   - procura diretamente no GSMArena
   - encontra a ficha técnica do aparelho
   - extrai a imagem principal
   - retorna a imagem em base64

2) /api/compor
   - recebe uma imagem do celular
   - compõe com o template do Hydrogel
   - retorna JPG

Estratégia de busca:

    CACHE
      ↓
    GSMArena diretamente
      ↓
    DuckDuckGo apenas como fallback
      ↓
    modo manual

Não depende de Bing API.

IMPORTANTE:
- O cache em memória ajuda durante a reutilização da mesma instância
  da Vercel, mas não é persistente.
- Sites de terceiros podem alterar sua estrutura ou limitar automação.
- O modo manual continua sendo o fallback definitivo.
"""

import base64
import io
import re
import time
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from flask import Flask, jsonify, request, send_file, send_from_directory
from PIL import Image


# ============================================================================
# APP
# ============================================================================

app = Flask(
    __name__,
    static_folder="static",
    static_url_path="",
)


# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

TEMPLATE_PATH = "static/template.jpg"

CANVAS_SIZE = (2000, 2000)

PHONE_BOX = (70, 220, 900, 1780)


# ============================================================================
# USER AGENT / HEADERS
# ============================================================================

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


GSMARENA_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,"
        "*/*;q=0.8"
    ),
    "Accept-Language": (
        "pt-BR,pt;q=0.9,en;q=0.8"
    ),
    "Referer": "https://www.gsmarena.com/",
    "Connection": "keep-alive",
}


SEARCH_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": (
        "pt-BR,pt;q=0.9,en;q=0.8"
    ),
}


# ============================================================================
# URLS
# ============================================================================

GSMARENA_BASE = "https://www.gsmarena.com"

GSMARENA_SEARCH_URL = (
    "https://www.gsmarena.com/"
    "results.php3?sQuickSearch=yes&sName="
)


# ============================================================================
# SESSION HTTP
# ============================================================================

SESSION = requests.Session()

SESSION.headers.update(
    GSMARENA_HEADERS
)


# ============================================================================
# REGEX
# ============================================================================

# Página de aparelho:
#
# apple_iphone_16_pro_max-13107.php
# samsung_galaxy_s24_ultra-12771.php
#
GSMARENA_PHONE_LINK_RE = re.compile(
    r'href=["\']([^"\']*[a-zA-Z0-9_]+-\d+\.php)["\']',
    re.IGNORECASE,
)


# Busca especificamente dentro de:
#
# <div class="makers">
#
GSMARENA_MAKERS_RE = re.compile(
    r'<div[^>]+class=["\'][^"\']*\bmakers\b[^"\']*["\']'
    r'>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)


# Imagem principal:
#
# <div class="specs-photo-main">
#     ...
#     <img src="...">
#
GSMARENA_PHOTO_RE = re.compile(
    r"specs-photo-main.*?"
    r'<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']',
    re.IGNORECASE | re.DOTALL,
)


# Fallback sem aspas
GSMARENA_PHOTO_UNQUOTED_RE = re.compile(
    r"specs-photo-main.*?"
    r'<img[^>]+(?:src|data-src)=([^\s>]+)',
    re.IGNORECASE | re.DOTALL,
)


# Open Graph
OG_IMAGE_RE = re.compile(
    r'<meta[^>]+'
    r'property=["\']og:image["\']'
    r'[^>]+'
    r'content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


# DuckDuckGo fallback
DUCKDUCKGO_UDDG_RE = re.compile(
    r"uddg=([^&\"']+)",
    re.IGNORECASE,
)


# URLs que não queremos considerar como ficha principal.
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

URL_CACHE = {}

CACHE_TTL_SECONDS = 60 * 60 * 24 * 7


def normalizar_modelo(modelo: str) -> str:
    """
    Normaliza o modelo para busca e cache.
    """

    modelo = (modelo or "").strip().lower()

    modelo = re.sub(
        r"\s+",
        " ",
        modelo,
    )

    return modelo


def cache_get(modelo: str):
    """
    Recupera URL do cache.
    """

    chave = normalizar_modelo(modelo)

    item = URL_CACHE.get(chave)

    if not item:
        return None

    timestamp, url = item

    if (
        time.time() - timestamp
        > CACHE_TTL_SECONDS
    ):
        URL_CACHE.pop(chave, None)

        return None

    return url


def cache_set(
    modelo: str,
    url: str,
):
    """
    Salva URL no cache.
    """

    chave = normalizar_modelo(modelo)

    URL_CACHE[chave] = (
        time.time(),
        url,
    )


# ============================================================================
# VALIDAÇÃO DE URL
# ============================================================================

def eh_url_gsmarena_valida(
    url: str,
) -> bool:
    """
    Verifica se a URL pertence ao GSMArena
    e parece ser uma página de aparelho.
    """

    if not url:
        return False

    url = url.strip()

    if not url.startswith(
        (
            "http://",
            "https://",
        )
    ):
        return False

    try:

        parsed = urlparse(url)

        hostname = (
            parsed.hostname or ""
        ).lower()

    except Exception:

        return False

    if hostname not in {
        "gsmarena.com",
        "www.gsmarena.com",
    }:
        return False

    if not GSMARENA_PHONE_LINK_RE.search(
        url
    ):
        return False

    url_lower = url.lower()

    if any(
        trecho in url_lower
        for trecho in GSMARENA_EXCLUIR
    ):
        return False

    return True


# ============================================================================
# EXTRAÇÃO DE LINKS
# ============================================================================

def normalizar_url_gsmarena(
    href: str,
):
    """
    Converte links relativos em URLs absolutas.
    """

    if not href:
        return None

    href = href.strip()

    href = href.strip(
        "\"'"
    )

    if href.startswith("//"):

        return "https:" + href

    if href.startswith("/"):

        return urljoin(
            GSMARENA_BASE,
            href,
        )

    if href.startswith("http"):

        return href

    return urljoin(
        GSMARENA_BASE + "/",
        href,
    )


def extrair_links_dos_makers(
    html: str,
):
    """
    Extrai links dos resultados de busca do GSMArena.

    A estrutura conhecida atualmente utiliza:

        div.makers
            ul
                li
                    a href="..."
    """

    encontrados = []

    blocos = GSMARENA_MAKERS_RE.findall(
        html
    )

    # ------------------------------------------------------------------------
    # Primeiro tenta somente dentro dos blocos .makers.
    # ------------------------------------------------------------------------

    for bloco in blocos:

        links = GSMARENA_PHONE_LINK_RE.findall(
            bloco
        )

        for href in links:

            url = normalizar_url_gsmarena(
                href
            )

            if not url:
                continue

            if not eh_url_gsmarena_valida(
                url
            ):
                continue

            if url not in encontrados:

                encontrados.append(url)

    if encontrados:

        return encontrados

    # ------------------------------------------------------------------------
    # Fallback: procurar no HTML inteiro.
    # ------------------------------------------------------------------------

    links = GSMARENA_PHONE_LINK_RE.findall(
        html
    )

    for href in links:

        url = normalizar_url_gsmarena(
            href
        )

        if not url:
            continue

        if not eh_url_gsmarena_valida(
            url
        ):
            continue

        if url not in encontrados:

            encontrados.append(url)

    return encontrados


# ============================================================================
# BUSCA DIRETA NO GSMARENA
# ============================================================================

def buscar_url_gsmarena(
    modelo: str,
):
    """
    Pesquisa diretamente no GSMArena.
    """

    modelo = normalizar_modelo(
        modelo
    )

    if not modelo:

        return None

    search_url = (
        GSMARENA_SEARCH_URL
        + quote(
            modelo,
            safe="",
        )
    )

    try:

        response = SESSION.get(
            search_url,
            headers=GSMARENA_HEADERS,
            timeout=15,
        )

    except requests.exceptions.RequestException:

        return None

    # ------------------------------------------------------------------------
    # Status HTTP
    # ------------------------------------------------------------------------

    if response.status_code != 200:

        return None

    html = response.text

    if not html:

        return None

    # ------------------------------------------------------------------------
    # Extrai resultados.
    # ------------------------------------------------------------------------

    resultados = (
        extrair_links_dos_makers(
            html
        )
    )

    if not resultados:

        return None

    # ------------------------------------------------------------------------
    # Primeiro resultado válido.
    # ------------------------------------------------------------------------

    return resultados[0]


# ============================================================================
# DUCKDUCKGO — ÚLTIMO FALLBACK
# ============================================================================

def buscar_url_duckduckgo(
    modelo: str,
):
    """
    Fallback caso o GSMArena não responda.

    Não tenta contornar bloqueios do DuckDuckGo.
    """

    query = (
        f"site:gsmarena.com "
        f"{modelo} specifications"
    )

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

    if response.status_code != 200:

        return None

    html = response.text

    if not html:

        return None

    html_lower = html.lower()

    # ------------------------------------------------------------------------
    # Detecta bloqueio.
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
    # Links diretos
    # ------------------------------------------------------------------------

    links = GSMARENA_PHONE_LINK_RE.findall(
        html
    )

    for href in links:

        url_gsmarena = (
            normalizar_url_gsmarena(
                href
            )
        )

        if eh_url_gsmarena_valida(
            url_gsmarena
        ):

            return url_gsmarena

    # ------------------------------------------------------------------------
    # Links dentro de uddg.
    # ------------------------------------------------------------------------

    for wrapped in DUCKDUCKGO_UDDG_RE.findall(
        html
    ):

        try:

            decoded = unquote(
                wrapped
            )

        except Exception:

            continue

        links = (
            GSMARENA_PHONE_LINK_RE.findall(
                decoded
            )
        )

        for href in links:

            url_gsmarena = (
                normalizar_url_gsmarena(
                    href
                )
            )

            if eh_url_gsmarena_valida(
                url_gsmarena
            ):

                return url_gsmarena

    return None


# ============================================================================
# BUSCA PRINCIPAL
# ============================================================================

def buscar_url_produto(
    modelo: str,
):
    """
    Estratégia:

        1. Cache
        2. GSMArena direto
        3. DuckDuckGo fallback
        4. None
    """

    # ------------------------------------------------------------------------
    # 1. CACHE
    # ------------------------------------------------------------------------

    cached = cache_get(
        modelo
    )

    if cached:

        if eh_url_gsmarena_valida(
            cached
        ):

            return cached

    # ------------------------------------------------------------------------
    # 2. GSMArena direto
    # ------------------------------------------------------------------------

    url = buscar_url_gsmarena(
        modelo
    )

    if url:

        cache_set(
            modelo,
            url,
        )

        return url

    # ------------------------------------------------------------------------
    # 3. DuckDuckGo fallback
    # ------------------------------------------------------------------------

    url = buscar_url_duckduckgo(
        modelo
    )

    if url:

        cache_set(
            modelo,
            url,
        )

        return url

    # ------------------------------------------------------------------------
    # 4. Nada encontrado.
    # ------------------------------------------------------------------------

    return None


# ============================================================================
# EXTRAÇÃO DA IMAGEM
# ============================================================================

def extrair_imagem_da_pagina(
    html: str,
):
    """
    Tenta encontrar a imagem principal da página do aparelho.
    """

    # ------------------------------------------------------------------------
    # 1. specs-photo-main
    # ------------------------------------------------------------------------

    match = GSMARENA_PHOTO_RE.search(
        html
    )

    if match:

        return match.group(1)

    # ------------------------------------------------------------------------
    # 2. versão sem aspas
    # ------------------------------------------------------------------------

    match = (
        GSMARENA_PHOTO_UNQUOTED_RE.search(
            html
        )
    )

    if match:

        return (
            match.group(1)
            .strip("\"'")
        )

    # ------------------------------------------------------------------------
    # 3. og:image
    # ------------------------------------------------------------------------

    match = OG_IMAGE_RE.search(
        html
    )

    if match:

        return match.group(1)

    return None


def normalizar_url_imagem(
    img_url: str,
):
    """
    Normaliza URL da imagem.
    """

    if not img_url:

        return None

    img_url = img_url.strip()

    img_url = img_url.strip(
        "\"'"
    )

    if img_url.startswith("//"):

        return "https:" + img_url

    if img_url.startswith("/"):

        return urljoin(
            GSMARENA_BASE,
            img_url,
        )

    return img_url


# ============================================================================
# DOWNLOAD DA IMAGEM
# ============================================================================

def baixar_imagem_produto(
    url_produto: str,
):
    """
    Abre a página do aparelho e baixa sua imagem principal.
    """

    if not eh_url_gsmarena_valida(
        url_produto
    ):

        return None

    # ------------------------------------------------------------------------
    # Página do aparelho
    # ------------------------------------------------------------------------

    try:

        response = SESSION.get(
            url_produto,
            headers=GSMARENA_HEADERS,
            timeout=15,
        )

    except requests.exceptions.RequestException:

        return None

    if response.status_code != 200:

        return None

    html = response.text

    if not html:

        return None

    # ------------------------------------------------------------------------
    # URL da imagem
    # ------------------------------------------------------------------------

    img_url = (
        extrair_imagem_da_pagina(
            html
        )
    )

    if not img_url:

        return None

    img_url = normalizar_url_imagem(
        img_url
    )

    if not img_url:

        return None

    # ------------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------------

    try:

        image_response = SESSION.get(
            img_url,
            headers=GSMARENA_HEADERS,
            timeout=15,
        )

    except requests.exceptions.RequestException:

        return None

    if image_response.status_code != 200:

        return None

    if not image_response.content:

        return None

    # ------------------------------------------------------------------------
    # Verifica Content-Type
    # ------------------------------------------------------------------------

    content_type = (
        image_response.headers
        .get(
            "Content-Type",
            "",
        )
        .lower()
    )

    if content_type:

        if not content_type.startswith(
            "image/"
        ):

            return None

    # ------------------------------------------------------------------------
    # Verifica se realmente é imagem.
    # ------------------------------------------------------------------------

    try:

        image = Image.open(
            io.BytesIO(
                image_response.content
            )
        )

        image.verify()

    except Exception:

        return None

    return image_response.content


# ============================================================================
# COMPOSIÇÃO
# ============================================================================

def compose_image(
    phone_bytes: bytes,
) -> Image.Image:
    """
    Cola a foto do celular ao lado do molde.
    """

    template = Image.open(
        TEMPLATE_PATH
    ).convert(
        "RGB"
    )

    if template.size != CANVAS_SIZE:

        template = template.resize(
            CANVAS_SIZE,
            Image.LANCZOS,
        )

    phone = Image.open(
        io.BytesIO(
            phone_bytes
        )
    )

    if "A" in phone.getbands():

        phone = phone.convert(
            "RGBA"
        )

    else:

        phone = phone.convert(
            "RGB"
        )

    x0, y0, x1, y1 = PHONE_BOX

    box_w = x1 - x0
    box_h = y1 - y0

    # ------------------------------------------------------------------------
    # Contain
    # ------------------------------------------------------------------------

    scale = min(
        box_w / phone.width,
        box_h / phone.height,
    )

    new_w = max(
        1,
        int(
            phone.width * scale
        ),
    )

    new_h = max(
        1,
        int(
            phone.height * scale
        ),
    )

    phone_resized = phone.resize(
        (
            new_w,
            new_h,
        ),
        Image.LANCZOS,
    )

    paste_x = (
        x0
        + (
            box_w - new_w
        ) // 2
    )

    paste_y = (
        y0
        + (
            box_h - new_h
        ) // 2
    )

    if phone_resized.mode == "RGBA":

        template.paste(
            phone_resized,
            (
                paste_x,
                paste_y,
            ),
            phone_resized,
        )

    else:

        template.paste(
            phone_resized,
            (
                paste_x,
                paste_y,
            ),
        )

    return template


# ============================================================================
# API — BUSCAR
# ============================================================================

@app.get("/api/buscar")
def api_buscar():

    modelo = (
        request.args.get(
            "modelo"
        )
        or ""
    ).strip()

    if not modelo:

        return jsonify(
            ok=False,
            motivo=(
                "Digite o nome do modelo."
            ),
        ), 400

    # ------------------------------------------------------------------------
    # Busca URL
    # ------------------------------------------------------------------------

    try:

        url_produto = (
            buscar_url_produto(
                modelo
            )
        )

    except Exception as exc:

        return jsonify(
            ok=False,
            motivo=(
                "Erro interno durante a busca."
            ),
            detalhe=(
                exc.__class__.__name__
            ),
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

    imagem_bytes = (
        baixar_imagem_produto(
            url_produto
        )
    )

    if not imagem_bytes:

        return jsonify(
            ok=False,
            motivo=(
                "Encontrei a página do modelo, "
                "mas não consegui obter a "
                "imagem principal. "
                "Use a busca manual."
            ),
            url_produto=url_produto,
        )

    # ------------------------------------------------------------------------
    # Base64
    # ------------------------------------------------------------------------

    imagem_b64 = (
        base64.b64encode(
            imagem_bytes
        )
        .decode(
            "ascii"
        )
    )

    return jsonify(
        ok=True,
        imagem_base64=imagem_b64,
        url_produto=url_produto,
        modelo=modelo,
    )


# ============================================================================
# API — COMPOR
# ============================================================================

@app.post("/api/compor")
def api_compor():

    phone_bytes = None

    # ------------------------------------------------------------------------
    # Upload manual
    # ------------------------------------------------------------------------

    if "foto" in request.files:

        phone_bytes = (
            request.files[
                "foto"
            ].read()
        )

    # ------------------------------------------------------------------------
    # JSON automático
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
                    base64.b64decode(
                        b64
                    )
                )

            except Exception:

                return jsonify(
                    ok=False,
                    motivo=(
                        "Imagem base64 inválida."
                    ),
                ), 400

    if not phone_bytes:

        return jsonify(
            ok=False,
            motivo=(
                "Nenhuma imagem de "
                "celular recebida."
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
                "Não consegui compor "
                f"a imagem: {exc}"
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
        download_name=(
            "gshield-hydrogel.jpg"
        ),
    )


# ============================================================================
# DEBUG — TESTE DIRETO DO GSMARENA
# ============================================================================

@app.get("/api/debug-gsmarena")
def api_debug_gsmarena():

    modelo = (
        request.args.get(
            "modelo"
        )
        or "iPhone 16 Pro Max"
    ).strip()

    resultado = {
        "ok": True,
        "modelo": modelo,
        "cache": None,
        "gsmarena_search_url": None,
        "gsmarena_status": None,
        "gsmarena_html_bytes": 0,
        "url_produto": None,
        "imagem": False,
        "imagem_bytes": 0,
        "erro": None,
    }

    # ------------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------------

    cached = cache_get(
        modelo
    )

    resultado["cache"] = {
        "encontrado": bool(cached),
        "url": cached,
    }

    # ------------------------------------------------------------------------
    # URL da pesquisa
    # ------------------------------------------------------------------------

    search_url = (
        GSMARENA_SEARCH_URL
        + quote(
            normalizar_modelo(
                modelo
            ),
            safe="",
        )
    )

    resultado[
        "gsmarena_search_url"
    ] = search_url

    # ------------------------------------------------------------------------
    # Request
    # ------------------------------------------------------------------------

    try:

        response = SESSION.get(
            search_url,
            headers=GSMARENA_HEADERS,
            timeout=15,
        )

    except requests.exceptions.RequestException as exc:

        resultado["erro"] = (
            f"{exc.__class__.__name__}: "
            f"{exc}"
        )

        return jsonify(
            resultado
        )

    resultado[
        "gsmarena_status"
    ] = response.status_code

    resultado[
        "gsmarena_html_bytes"
    ] = len(
        response.content
    )

    if response.status_code != 200:

        resultado["erro"] = (
            "GSMArena retornou HTTP "
            f"{response.status_code}"
        )

        return jsonify(
            resultado
        )

    # ------------------------------------------------------------------------
    # Extrai URL
    # ------------------------------------------------------------------------

    url_produto = (
        extrair_links_dos_makers(
            response.text
        )
    )

    if url_produto:

        url_produto = url_produto[0]

    resultado[
        "url_produto"
    ] = url_produto

    if not url_produto:

        resultado["erro"] = (
            "A página respondeu, mas "
            "nenhuma ficha de aparelho "
            "foi encontrada."
        )

        return jsonify(
            resultado
        )

    # ------------------------------------------------------------------------
    # Imagem
    # ------------------------------------------------------------------------

    imagem = (
        baixar_imagem_produto(
            url_produto
        )
    )

    if imagem:

        resultado[
            "imagem"
        ] = True

        resultado[
            "imagem_bytes"
        ] = len(imagem)

    else:

        resultado["erro"] = (
            "A ficha foi encontrada, "
            "mas a imagem não foi baixada."
        )

    return jsonify(
        resultado
    )


# ============================================================================
# DEBUG — STATUS DO SISTEMA
# ============================================================================

@app.get("/api/debug")
def api_debug():

    modelo = (
        request.args.get(
            "modelo"
        )
        or "iPhone 16 Pro Max"
    ).strip()

    cached = cache_get(
        modelo
    )

    return jsonify(
        ok=True,
        modelo=modelo,
        cache_encontrado=bool(
            cached
        ),
        cache_url=cached,
        cache_itens=len(
            URL_CACHE
        ),
        busca_principal=(
            "GSMArena direto"
        ),
        fallback=(
            "DuckDuckGo"
        ),
    )


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
# EXECUÇÃO LOCAL
# ============================================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
    )
