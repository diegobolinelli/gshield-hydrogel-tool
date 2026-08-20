import base64
import io
import os
import re
import time
from urllib.parse import urljoin, urlparse

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

GSMARENA_BASE = "https://www.gsmarena.com"

SUPABASE_TABLE = "devices"


# ============================================================================
# SUPABASE
# ============================================================================

SUPABASE_URL = (
    os.environ.get("SUPABASE_URL", "")
    .strip()
    .rstrip("/")
)

SUPABASE_SECRET_KEY = (
    os.environ.get(
        "SUPABASE_SERVICE_ROLE_KEY",
        "",
    )
    .strip()
)


# ============================================================================
# HTTP
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
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://www.gsmarena.com/",
    "Connection": "keep-alive",
}

SESSION = requests.Session()
SESSION.headers.update(GSMARENA_HEADERS)


# ============================================================================
# CATÁLOGOS
# ============================================================================

BRAND_CATALOGS = {

    "apple": "apple-phones-48.php",

    "samsung": "samsung-phones-9.php",

    "xiaomi": "xiaomi-phones-80.php",

    "motorola": "motorola-phones-4.php",

    "oneplus": "oneplus-phones-95.php",

    "google": "google-phones-107.php",

    "oppo": "oppo-phones-82.php",

    "realme": "realme-phones-118.php",

    "vivo": "vivo-phones-98.php",

    "honor": "honor-phones-121.php",

    "huawei": "huawei-phones-58.php",

    "asus": "asus-phones-46.php",

    "sony": "sony-phones-7.php",

    "nokia": "nokia-phones-1.php",

    "lg": "lg-phones-20.php",

    "zte": "zte-phones-62.php",

    "nubia": "nubia-phones-109.php",

    "tecno": "tecno-phones-120.php",

    "infinix": "infinix-phones-119.php",

    "nothing": "nothing-phones-128.php",

    "lenovo": "lenovo-phones-73.php",

    "blackview": "blackview-phones-116.php",

    "tcl": "tcl-phones-123.php",

    "meizu": "meizu-phones-74.php",

    "alcatel": "alcatel-phones-5.php",
}


# ============================================================================
# CACHE
# ============================================================================

BRAND_MEMORY_CACHE = {}

BRAND_CACHE_TTL = 60 * 60 * 6


# ============================================================================
# NORMALIZAÇÃO
# ============================================================================

def normalizar_modelo(modelo: str) -> str:

    modelo = (
        modelo or ""
    ).strip().lower()

    modelo = modelo.replace("_", " ")
    modelo = modelo.replace("-", " ")

    modelo = re.sub(
        r"\s+",
        " ",
        modelo,
    )

    return modelo.strip()


def remover_acentos(texto: str) -> str:

    import unicodedata

    texto = unicodedata.normalize(
        "NFD",
        texto,
    )

    return "".join(
        char
        for char in texto
        if unicodedata.category(char) != "Mn"
    )


def normalizar_comparacao(texto: str) -> str:

    texto = normalizar_modelo(texto)

    texto = remover_acentos(texto)

    return texto


# ============================================================================
# FABRICANTE
# ============================================================================

def identificar_fabricante(modelo: str):

    texto = normalizar_comparacao(modelo)

    for brand in BRAND_CATALOGS:

        if re.search(
            rf"\b{re.escape(brand)}\b",
            texto,
        ):
            return brand

    if re.match(r"^(iphone|ipad)\b", texto):
        return "apple"

    if re.match(r"^(galaxy|sm[- ]?)", texto):
        return "samsung"

    if re.match(r"^(redmi|poco|mi)\b", texto):
        return "xiaomi"

    if re.match(r"^pixel\b", texto):
        return "google"

    if re.match(r"^moto\b", texto):
        return "motorola"

    if re.match(r"^xperia\b", texto):
        return "sony"

    if re.match(r"^nokia\b", texto):
        return "nokia"

    if re.match(r"^nothing\b", texto):
        return "nothing"

    return None


# ============================================================================
# SUPABASE
# ============================================================================

def supabase_headers():

    if not SUPABASE_SECRET_KEY:

        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY não configurada."
        )

    return {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def supabase_buscar_modelo(modelo: str):

    if not SUPABASE_URL:
        return None

    chave = normalizar_modelo(modelo)

    if not chave:
        return None

    url = (
        f"{SUPABASE_URL}"
        f"/rest/v1/{SUPABASE_TABLE}"
    )

    params = {
        "model_normalized": f"eq.{chave}",
        "select": "*",
        "limit": "1",
    }

    try:

        response = SESSION.get(
            url,
            headers=supabase_headers(),
            params=params,
            timeout=10,
        )

    except requests.exceptions.RequestException:

        return None

    if response.status_code != 200:
        return None

    try:

        data = response.json()

    except ValueError:

        return None

    if not data:
        return None

    return data[0]


def supabase_salvar_modelo(
    model_name: str,
    model_normalized: str,
    device_url: str,
    image_url: str,
):

    if not SUPABASE_URL:
        return False

    url = (
        f"{SUPABASE_URL}"
        f"/rest/v1/{SUPABASE_TABLE}"
    )

    payload = {
        "model_name": model_name,
        "model_normalized": model_normalized,
        "device_url": device_url,
        "image_url": image_url,
    }

    headers = supabase_headers()

    headers["Prefer"] = (
        "resolution=merge-duplicates,"
        "return=minimal"
    )

    try:

        response = SESSION.post(
            url,
            headers=headers,
            json=payload,
            timeout=10,
        )

    except requests.exceptions.RequestException:

        return False

    return response.status_code in {
        200,
        201,
        204,
    }


# ============================================================================
# URLS GSMARENA
# ============================================================================

def eh_url_aparelho_gsmarena(url: str):

    if not url:
        return False

    try:

        parsed = urlparse(url)

    except Exception:

        return False

    hostname = (
        parsed.hostname or ""
    ).lower()

    if hostname not in {
        "gsmarena.com",
        "www.gsmarena.com",
    }:
        return False

    caminho = parsed.path or ""

    if not re.search(
        r"-\d+\.php$",
        caminho,
        re.IGNORECASE,
    ):
        return False

    caminho_lower = caminho.lower()

    proibidos = (
        "-phones-",
        "-review-",
        "-news-",
        "-compare",
        "compare.",
        "glossary",
        "-pictures-",
    )

    if any(
        item in caminho_lower
        for item in proibidos
    ):
        return False

    return True


def url_absoluta(url: str):

    if not url:
        return None

    url = url.strip()

    if url.startswith("//"):
        return "https:" + url

    if url.startswith("/"):
        return urljoin(
            GSMARENA_BASE,
            url,
        )

    if url.startswith("http"):
        return url

    return urljoin(
        GSMARENA_BASE + "/",
        url,
    )


# ============================================================================
# HTML
# ============================================================================

def baixar_html(url: str):

    try:

        response = SESSION.get(
            url,
            headers=GSMARENA_HEADERS,
            timeout=20,
        )

    except requests.exceptions.RequestException:

        return None

    if response.status_code != 200:
        return None

    if not response.text:
        return None

    return response.text


# ============================================================================
# EXTRAÇÃO DOS APARELHOS
# ============================================================================

def limpar_nome_aparelho(texto: str):

    if not texto:
        return ""

    texto = re.sub(
        r"<[^>]+>",
        " ",
        texto,
    )

    texto = re.sub(
        r"\s+",
        " ",
        texto,
    )

    return texto.strip()


def extrair_dispositivos_catalogo(html: str):

    dispositivos = []

    padrao = re.compile(
        r'<a[^>]+href=["\']([^"\']+\.php)["\'][^>]*>'
        r"(.*?)"
        r"</a>",
        re.IGNORECASE | re.DOTALL,
    )

    for match in padrao.finditer(html):

        href = match.group(1)

        conteudo = match.group(2)

        url = url_absoluta(href)

        if not eh_url_aparelho_gsmarena(url):
            continue

        nome = limpar_nome_aparelho(
            conteudo
        )

        if not nome:
            continue

        if (
            "show only the devices" in
            nome.lower()
        ):
            continue

        if nome.lower().endswith(" review"):
            continue

        imagens = re.findall(
            r'(?:src|data-src)=["\']([^"\']+)["\']',
            conteudo,
            re.IGNORECASE,
        )

        image_url = None

        if imagens:

            image_url = url_absoluta(
                imagens[0]
            )

        dispositivos.append(
            {
                "name": nome,
                "url": url,
                "image_url": image_url,
            }
        )

    resultado = []

    vistos = set()

    for device in dispositivos:

        url = device["url"]

        if url in vistos:
            continue

        vistos.add(url)

        resultado.append(device)

    return resultado


# ============================================================================
# NOVA PAGINAÇÃO
#
# O diagnóstico mostrou que o GSMArena não está entregando os links
# de paginação de forma confiável.
#
# Então geramos diretamente:
#
# apple-phones-48.php
# apple-phones-48-2.php
# apple-phones-48-3.php
# ...
#
# e paramos quando duas páginas consecutivas não trouxerem aparelhos novos.
# ============================================================================

def gerar_url_pagina_catalogo(
    slug: str,
    numero: int,
):

    if numero <= 1:

        return (
            f"{GSMARENA_BASE}/{slug}"
        )

    match = re.match(
        r"^(.+?)-(\d+)\.php$",
        slug,
        re.IGNORECASE,
    )

    if not match:

        return (
            f"{GSMARENA_BASE}/{slug}"
        )

    prefixo = match.group(1)

    numero_marca = match.group(2)

    return (
        f"{GSMARENA_BASE}/"
        f"{prefixo}-{numero_marca}-{numero}.php"
    )


def baixar_catalogo_marca(brand: str):

    if brand not in BRAND_CATALOGS:
        return []

    agora = time.time()

    cache = BRAND_MEMORY_CACHE.get(brand)

    if cache:

        timestamp, dispositivos = cache

        if (
            agora - timestamp
            < BRAND_CACHE_TTL
        ):
            return dispositivos

    slug = BRAND_CATALOGS[brand]

    todos = []

    vistos = set()

    paginas_visitadas = []

    paginas_sem_novos = 0

    # ------------------------------------------------------------------------
    # Até 30 páginas.
    #
    # Isso cobre tranquilamente catálogos grandes e evita loops.
    # ------------------------------------------------------------------------

    MAX_PAGINAS = 30

    for numero_pagina in range(
        1,
        MAX_PAGINAS + 1,
    ):

        pagina_url = (
            gerar_url_pagina_catalogo(
                slug,
                numero_pagina,
            )
        )

        paginas_visitadas.append(
            pagina_url
        )

        html = baixar_html(
            pagina_url
        )

        if not html:

            paginas_sem_novos += 1

            if paginas_sem_novos >= 2:
                break

            continue

        dispositivos = (
            extrair_dispositivos_catalogo(
                html
            )
        )

        novos = 0

        for device in dispositivos:

            url = device.get("url")

            if not url:
                continue

            if url in vistos:
                continue

            vistos.add(url)

            todos.append(device)

            novos += 1

        # --------------------------------------------------------------------
        # Se a página não trouxe nenhum aparelho novo,
        # provavelmente chegamos ao fim.
        # --------------------------------------------------------------------

        if novos == 0:

            paginas_sem_novos += 1

        else:

            paginas_sem_novos = 0

        # --------------------------------------------------------------------
        # Duas páginas vazias consecutivas = fim.
        # --------------------------------------------------------------------

        if paginas_sem_novos >= 2:

            break

        time.sleep(0.15)

    BRAND_MEMORY_CACHE[brand] = (
        agora,
        todos,
    )

    return todos


# ============================================================================
# ENCONTRAR MODELO
# ============================================================================

def remover_fabricante_do_nome(
    texto: str,
):

    texto = normalizar_comparacao(
        texto
    )

    for brand in BRAND_CATALOGS:

        texto = re.sub(
            rf"^{re.escape(brand)}\s+",
            "",
            texto,
        )

    return texto.strip()


def encontrar_no_catalogo(
    modelo: str,
    dispositivos: list,
):

    procurado = remover_fabricante_do_nome(
        modelo
    )

    if not procurado:
        return None

    # ------------------------------------------------------------------------
    # Correspondência exata.
    # ------------------------------------------------------------------------

    for device in dispositivos:

        nome = remover_fabricante_do_nome(
            device.get(
                "name",
                "",
            )
        )

        if nome == procurado:

            return device

    # ------------------------------------------------------------------------
    # Correspondência por tokens.
    #
    # Mantemos o mesmo número de tokens para evitar:
    #
    # iPhone 13 -> iPhone 13 Pro
    #
    # ------------------------------------------------------------------------

    procurado_tokens = procurado.split()

    for device in dispositivos:

        nome = remover_fabricante_do_nome(
            device.get(
                "name",
                "",
            )
        )

        tokens = nome.split()

        if tokens == procurado_tokens:

            return device

    return None


# ============================================================================
# IMAGEM DO APARELHO
# ============================================================================

def encontrar_imagem_pagina_aparelho(
    device_url: str,
):

    html = baixar_html(
        device_url
    )

    if not html:
        return None

    padroes = [

        (
            r'specs-photo-main.*?'
            r'<img[^>]+src=["\']([^"\']+)'
        ),

        (
            r'specs-photo-main.*?'
            r'(?:data-src|src)=["\']([^"\']+)'
        ),

        (
            r'<meta[^>]+property=["\']og:image["\']'
            r'[^>]+content=["\']([^"\']+)'
        ),

        (
            r'<meta[^>]+content=["\']([^"\']+)'
            r'[^>]+property=["\']og:image["\']'
        ),
    ]

    for padrao in padroes:

        match = re.search(
            padrao,
            html,
            re.IGNORECASE | re.DOTALL,
        )

        if not match:
            continue

        imagem = url_absoluta(
            match.group(1)
        )

        if imagem:
            return imagem

    return None


def baixar_imagem_url(
    image_url: str,
):

    if not image_url:
        return None

    try:

        response = SESSION.get(
            image_url,
            headers=GSMARENA_HEADERS,
            timeout=20,
        )

    except requests.exceptions.RequestException:

        return None

    if response.status_code != 200:
        return None

    if not response.content:
        return None

    try:

        image = Image.open(
            io.BytesIO(
                response.content
            )
        )

        image.verify()

    except Exception:

        return None

    return response.content


def obter_imagem_do_device(
    device,
):

    # ------------------------------------------------------------------------
    # Primeiro tenta a imagem do catálogo.
    # ------------------------------------------------------------------------

    image_url = device.get(
        "image_url"
    )

    if image_url:

        # Não aceitar GIF de fallback do GSMArena.
        if not image_url.lower().endswith(
            ".gif"
        ):

            imagem = baixar_imagem_url(
                image_url
            )

            if imagem:
                return imagem, image_url

    # ------------------------------------------------------------------------
    # Depois abre a página do aparelho.
    # ------------------------------------------------------------------------

    device_url = device.get(
        "url"
    )

    if not device_url:
        return None, None

    imagem_pagina = (
        encontrar_imagem_pagina_aparelho(
            device_url
        )
    )

    if imagem_pagina:

        imagem = baixar_imagem_url(
            imagem_pagina
        )

        if imagem:

            return imagem, imagem_pagina

    return None, None


# ============================================================================
# BUSCAR MODELO NOVO
# ============================================================================

def buscar_modelo_novo(
    modelo: str,
):

    brand = identificar_fabricante(
        modelo
    )

    if not brand:

        return None, {
            "etapa": "fabricante",
            "motivo": (
                "Não consegui identificar "
                "o fabricante."
            ),
        }

    dispositivos = (
        baixar_catalogo_marca(
            brand
        )
    )

    if not dispositivos:

        return None, {
            "etapa": "catalogo",
            "fabricante": brand,
            "motivo": (
                "Não consegui carregar "
                "o catálogo do fabricante."
            ),
        }

    encontrado = (
        encontrar_no_catalogo(
            modelo,
            dispositivos,
        )
    )

    if not encontrado:

        return None, {
            "etapa": "catalogo",
            "fabricante": brand,
            "total_catalogo": len(
                dispositivos
            ),
            "motivo": (
                "Modelo exato não encontrado "
                "no catálogo."
            ),
        }

    imagem, image_url = (
        obter_imagem_do_device(
            encontrado
        )
    )

    if not imagem:

        return None, {
            "etapa": "imagem",
            "fabricante": brand,
            "modelo": encontrado.get(
                "name"
            ),
            "device_url": encontrado.get(
                "url"
            ),
            "motivo": (
                "Modelo encontrado, "
                "mas não consegui obter "
                "a imagem."
            ),
        }

    nome = (
        encontrado.get(
            "name"
        )
        or modelo
    )

    normalizado = normalizar_modelo(
        modelo
    )

    device_url = encontrado.get(
        "url"
    )

    salvo = (
        supabase_salvar_modelo(
            model_name=nome,
            model_normalized=normalizado,
            device_url=device_url,
            image_url=image_url,
        )
    )

    return {
        "imagem": imagem,
        "model_name": nome,
        "model_normalized": normalizado,
        "device_url": device_url,
        "image_url": image_url,
        "fabricante": brand,
        "salvo": salvo,
        "origem": "gsmarena_catalogo",
    }, None


# ============================================================================
# FLUXO PRINCIPAL
# ============================================================================

def obter_modelo_e_imagem(
    modelo: str,
):

    # 1. Supabase
    existente = (
        supabase_buscar_modelo(
            modelo
        )
    )

    if existente:

        imagem = (
            baixar_imagem_url(
                existente.get(
                    "image_url"
                )
            )
        )

        if imagem:

            return {
                "imagem": imagem,
                "modelo": existente.get(
                    "model_name"
                ),
                "url": existente.get(
                    "device_url"
                ),
                "origem": "supabase",
            }, None

    # 2. GSMArena
    resultado, erro = (
        buscar_modelo_novo(
            modelo
        )
    )

    if not resultado:

        return None, erro

    return resultado, None


# ============================================================================
# COMPOSIÇÃO
# ============================================================================

def compose_image(
    phone_bytes: bytes,
) -> Image.Image:

    template = Image.open(
        TEMPLATE_PATH
    ).convert("RGB")

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
# API - BUSCAR
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
            motivo="Digite o nome do modelo.",
        ), 400

    try:

        resultado, erro = (
            obter_modelo_e_imagem(
                modelo
            )
        )

    except Exception as exc:

        return jsonify(
            ok=False,
            motivo="Erro interno durante a busca.",
            detalhe=exc.__class__.__name__,
        ), 500

    if not resultado:

        return jsonify(
            ok=False,
            motivo=(
                "Não encontrei automaticamente "
                "esse modelo. Use a busca manual."
            ),
            detalhe=erro,
        )

    imagem_b64 = (
        base64.b64encode(
            resultado["imagem"]
        ).decode("ascii")
    )

    return jsonify(
        ok=True,
        imagem_base64=imagem_b64,
        url_produto=(
            resultado.get("url")
            or resultado.get("device_url")
        ),
        modelo=(
            resultado.get("modelo")
            or resultado.get("model_name")
        ),
        origem=resultado.get(
            "origem"
        ),
    )


# ============================================================================
# API - COMPOR
# ============================================================================

@app.post("/api/compor")
def api_compor():

    phone_bytes = None

    if "foto" in request.files:

        phone_bytes = (
            request.files["foto"].read()
        )

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
                    motivo="Imagem base64 inválida.",
                ), 400

    if not phone_bytes:

        return jsonify(
            ok=False,
            motivo=(
                "Nenhuma imagem de celular recebida."
            ),
        ), 400

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
# DEBUG - SUPABASE
# ============================================================================

@app.get("/api/debug-supabase")
def api_debug_supabase():

    if not SUPABASE_URL:

        return jsonify(
            ok=False,
            erro="SUPABASE_URL não configurada.",
        )

    if not SUPABASE_SECRET_KEY:

        return jsonify(
            ok=False,
            erro=(
                "SUPABASE_SERVICE_ROLE_KEY "
                "não configurada."
            ),
        )

    try:

        url = (
            f"{SUPABASE_URL}"
            f"/rest/v1/{SUPABASE_TABLE}"
        )

        response = SESSION.get(
            url,
            headers=supabase_headers(),
            params={
                "select": "id",
                "limit": "1",
            },
            timeout=10,
        )

        return jsonify(
            ok=response.status_code == 200,
            status_code=response.status_code,
            resposta=response.text[:1000],
        )

    except Exception as exc:

        return jsonify(
            ok=False,
            erro=exc.__class__.__name__,
            detalhe=str(exc),
        )


# ============================================================================
# DEBUG - PAGINAÇÃO
# ============================================================================

@app.get("/api/debug-paginacao")
def api_debug_paginacao():

    modelo = (
        request.args.get(
            "modelo"
        )
        or "iPhone 13"
    ).strip()

    brand = identificar_fabricante(
        modelo
    )

    if not brand:

        return jsonify(
            ok=False,
            erro="Fabricante não identificado.",
        )

    slug = BRAND_CATALOGS[brand]

    paginas = []

    resultados = []

    for numero in range(1, 8):

        url = (
            gerar_url_pagina_catalogo(
                slug,
                numero,
            )
        )

        html = baixar_html(
            url
        )

        quantidade = 0

        encontrou = False

        if html:

            dispositivos = (
                extrair_dispositivos_catalogo(
                    html
                )
            )

            quantidade = len(
                dispositivos
            )

            encontrado = (
                encontrar_no_catalogo(
                    modelo,
                    dispositivos,
                )
            )

            encontrou = bool(
                encontrado
            )

        paginas.append(
            {
                "pagina": numero,
                "url": url,
                "dispositivos": quantidade,
                "encontrou_modelo": encontrou,
            }
        )

        resultados.append(
            numero
        )

        if encontrou:
            break

        if quantidade == 0 and numero > 1:
            break

    return jsonify(
        ok=True,
        modelo=modelo,
        fabricante=brand,
        paginas=paginas,
    )


# ============================================================================
# DEBUG - GSMARENA
# ============================================================================

@app.get("/api/debug-gsmarena")
def api_debug_gsmarena():

    modelo = (
        request.args.get(
            "modelo"
        )
        or "iPhone 13"
    ).strip()

    brand = identificar_fabricante(
        modelo
    )

    resultado = {
        "ok": True,
        "modelo": modelo,
        "fabricante": brand,
        "catalogo": False,
        "total_dispositivos": 0,
        "encontrado": None,
        "imagem": False,
        "imagem_bytes": 0,
    }

    if not brand:

        resultado["erro"] = (
            "Fabricante não identificado."
        )

        return jsonify(
            resultado
        )

    dispositivos = (
        baixar_catalogo_marca(
            brand
        )
    )

    resultado["catalogo"] = bool(
        dispositivos
    )

    resultado["total_dispositivos"] = len(
        dispositivos
    )

    encontrado = (
        encontrar_no_catalogo(
            modelo,
            dispositivos,
        )
    )

    if not encontrado:

        resultado["erro"] = (
            "Modelo exato não encontrado no catálogo."
        )

        return jsonify(
            resultado
        )

    resultado["encontrado"] = {
        "name": encontrado.get("name"),
        "url": encontrado.get("url"),
        "image_url": encontrado.get("image_url"),
    }

    imagem, imagem_url = (
        obter_imagem_do_device(
            encontrado
        )
    )

    resultado["imagem"] = bool(
        imagem
    )

    resultado["imagem_bytes"] = (
        len(imagem)
        if imagem
        else 0
    )

    resultado["imagem_url_final"] = (
        imagem_url
    )

    return jsonify(
        resultado
    )


# ============================================================================
# DEBUG - MODELOS
# ============================================================================

@app.get("/api/debug-modelos")
def api_debug_modelos():

    brand = (
        request.args.get(
            "marca"
        )
        or "apple"
    ).strip().lower()

    if brand not in BRAND_CATALOGS:

        return jsonify(
            ok=False,
            erro="Marca não cadastrada.",
        )

    dispositivos = (
        baixar_catalogo_marca(
            brand
        )
    )

    return jsonify(
        ok=True,
        marca=brand,
        total=len(dispositivos),
        modelos=dispositivos,
    )


# ============================================================================
# DEBUG - STATUS
# ============================================================================

@app.get("/api/debug")
def api_debug():

    return jsonify(
        ok=True,
        supabase_configurada=bool(
            SUPABASE_URL
        ),
        supabase_secret_configurada=bool(
            SUPABASE_SECRET_KEY
        ),
        fabricantes_disponiveis=len(
            BRAND_CATALOGS
        ),
        cache_marcas=len(
            BRAND_MEMORY_CACHE
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
# LOCAL
# ============================================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
    )
