import base64
import io
import os
import re
import time
import unicodedata
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

SESSION.headers.update(
    GSMARENA_HEADERS
)


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

    return texto.strip()


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

    if re.match(
        r"^(iphone|ipad)\b",
        texto,
    ):
        return "apple"

    if re.match(
        r"^(galaxy|sm[- ]?)",
        texto,
    ):
        return "samsung"

    if re.match(
        r"^(redmi|poco|mi)\b",
        texto,
    ):
        return "xiaomi"

    if re.match(
        r"^pixel\b",
        texto,
    ):
        return "google"

    if re.match(
        r"^moto\b",
        texto,
    ):
        return "motorola"

    if re.match(
        r"^xperia\b",
        texto,
    ):
        return "sony"

    if re.match(
        r"^nokia\b",
        texto,
    ):
        return "nokia"

    if re.match(
        r"^nothing\b",
        texto,
    ):
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
        "Authorization": (
            f"Bearer {SUPABASE_SECRET_KEY}"
        ),
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
# GSMARENA - URL DE APARELHO
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
# DOWNLOAD HTML
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
# EXTRAIR APARELHOS
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


def extrair_dispositivos_catalogo(
    html: str,
):

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

        url = url_absoluta(
            href
        )

        if not eh_url_aparelho_gsmarena(
            url
        ):

            continue

        nome = limpar_nome_aparelho(
            conteudo
        )

        if not nome:

            continue

        nome_lower = nome.lower()

        if (
            "show only the devices" in
            nome_lower
        ):

            continue

        if nome_lower.endswith(
            " review"
        ):

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

        resultado.append(
            device
        )

    return resultado


# ============================================================================
# PAGINAÇÃO
# ============================================================================

def gerar_url_catalogo(
    slug: str,
    pagina: int,
):

    if pagina <= 1:

        return (
            f"{GSMARENA_BASE}/{slug}"
        )

    match = re.match(
        r"^(.+?)-phones-(\d+)\.php$",
        slug,
        re.IGNORECASE,
    )

    if not match:

        return (
            f"{GSMARENA_BASE}/{slug}"
        )

    marca = match.group(1)

    codigo = match.group(2)

    return (
        f"{GSMARENA_BASE}/"
        f"{marca}-phones-f-{codigo}-0-p{pagina}.php"
    )


# ============================================================================
# CATÁLOGO DA MARCA
# ============================================================================

def baixar_catalogo_marca(
    brand: str,
):

    if brand not in BRAND_CATALOGS:

        return []

    agora = time.time()

    cache = BRAND_MEMORY_CACHE.get(
        brand
    )

    if cache:

        timestamp, dispositivos = cache

        if (
            agora - timestamp
            < BRAND_CACHE_TTL
        ):

            return dispositivos

    slug = BRAND_CATALOGS[
        brand
    ]

    todos = []

    vistos = set()

    paginas_sem_novos = 0

    MAX_PAGINAS = 50

    for pagina in range(
        1,
        MAX_PAGINAS + 1,
    ):

        url = gerar_url_catalogo(
            slug,
            pagina,
        )

        html = baixar_html(
            url
        )

        if not html:

            paginas_sem_novos += 1

            if (
                pagina > 1
                and paginas_sem_novos >= 2
            ):

                break

            continue

        dispositivos = (
            extrair_dispositivos_catalogo(
                html
            )
        )

        novos = 0

        for device in dispositivos:

            device_url = device.get(
                "url"
            )

            if not device_url:

                continue

            if device_url in vistos:

                continue

            vistos.add(
                device_url
            )

            todos.append(
                device
            )

            novos += 1

        if novos == 0:

            paginas_sem_novos += 1

        else:

            paginas_sem_novos = 0

        if paginas_sem_novos >= 2:

            break

        time.sleep(0.15)

    BRAND_MEMORY_CACHE[
        brand
    ] = (
        agora,
        todos,
    )

    return todos


# ============================================================================
# ENCONTRAR MODELO
# ============================================================================

def remover_fabricante(
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

    procurado = remover_fabricante(
        modelo
    )

    if not procurado:

        return None

    # Igualdade exata
    for device in dispositivos:

        nome = remover_fabricante(
            device.get(
                "name",
                "",
            )
        )

        if nome == procurado:

            return device

    # Igualdade por tokens
    tokens_procurados = procurado.split()

    for device in dispositivos:

        nome = remover_fabricante(
            device.get(
                "name",
                "",
            )
        )

        tokens_nome = nome.split()

        if tokens_nome == tokens_procurados:

            return device

    return None


# ============================================================================
# IMAGEM - VALIDAÇÃO
# ============================================================================

def imagem_parece_placeholder(
    image_url: str,
) -> bool:

    if not image_url:

        return True

    texto = image_url.lower()

    palavras_bloqueadas = (
        "amazon",
        "logo",
        "logo-fallback",
        "fallback",
        "placeholder",
        "icon",
        "icons",
        "avatar",
        "favicon",
        "sprite",
        "loading",
        "blank",
        "default",
    )

    return any(
        palavra in texto
        for palavra in palavras_bloqueadas
    )


def imagem_eh_valida(
    image_url: str,
) -> bool:

    if imagem_parece_placeholder(
        image_url
    ):

        return False

    return (
        image_url.startswith(
            "http://"
        )
        or image_url.startswith(
            "https://"
        )
    )


# ============================================================================
# IMAGEM DA PÁGINA INDIVIDUAL
# ============================================================================

def encontrar_imagem_pagina(
    device_url: str,
):

    html = baixar_html(
        device_url
    )

    if not html:

        return None

    # ------------------------------------------------------------------------
    # 1. FOTO PRINCIPAL
    # ------------------------------------------------------------------------

    padroes_principais = [

        re.compile(
            r'class=["\'][^"\']*specs-photo-main[^"\']*["\']'
            r'.*?'
            r'(?:data-src|src)=["\']([^"\']+)',
            re.IGNORECASE | re.DOTALL,
        ),

        re.compile(
            r'specs-photo-main.*?'
            r'(?:data-src|src)=["\']([^"\']+)',
            re.IGNORECASE | re.DOTALL,
        ),

    ]

    for padrao in padroes_principais:

        match = padrao.search(
            html
        )

        if not match:

            continue

        imagem = url_absoluta(
            match.group(1)
        )

        if imagem_eh_valida(
            imagem
        ):

            return imagem

    # ------------------------------------------------------------------------
    # 2. OG IMAGE
    # ------------------------------------------------------------------------

    padroes_og = [

        re.compile(
            r'<meta[^>]+property=["\']og:image["\']'
            r'[^>]+content=["\']([^"\']+)',
            re.IGNORECASE,
        ),

        re.compile(
            r'<meta[^>]+content=["\']([^"\']+)'
            r'[^>]+property=["\']og:image["\']',
            re.IGNORECASE,
        ),

    ]

    for padrao in padroes_og:

        match = padrao.search(
            html
        )

        if not match:

            continue

        imagem = url_absoluta(
            match.group(1)
        )

        if imagem_eh_valida(
            imagem
        ):

            return imagem

    # ------------------------------------------------------------------------
    # 3. PROCURA POR IMAGEM BIGPIC
    #
    # O GSMArena normalmente utiliza URLs
    # contendo bigpic para as imagens principais.
    # ------------------------------------------------------------------------

    bigpics = re.findall(
        r'["\']([^"\']*bigpic[^"\']+)["\']',
        html,
        re.IGNORECASE,
    )

    for imagem in bigpics:

        imagem = url_absoluta(
            imagem
        )

        if imagem_eh_valida(
            imagem
        ):

            return imagem

    return None


# ============================================================================
# DOWNLOAD E VALIDAÇÃO DA IMAGEM
# ============================================================================

def baixar_imagem_url(
    image_url: str,
):

    if not imagem_eh_valida(
        image_url
    ):

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

    content_type = (
        response.headers
        .get(
            "Content-Type",
            "",
        )
        .lower()
    )

    # ------------------------------------------------------------------------
    # Não aceitar HTML
    # ------------------------------------------------------------------------

    if (
        content_type
        and not content_type.startswith(
            "image/"
        )
    ):

        return None

    # ------------------------------------------------------------------------
    # Confirma que Pillow consegue abrir
    # ------------------------------------------------------------------------

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


# ============================================================================
# OBTER IMAGEM DO APARELHO
# ============================================================================

def obter_imagem_device(
    device,
):

    device_url = device.get(
        "url"
    )

    if not device_url:

        return (
            None,
            None,
        )

    # ------------------------------------------------------------------------
    # IMPORTANTE:
    #
    # Não usamos mais a miniatura do catálogo como primeira opção.
    #
    # Vamos diretamente para a página individual.
    # Isso evita Amazon/logo/fallback.
    # ------------------------------------------------------------------------

    imagem_url = (
        encontrar_imagem_pagina(
            device_url
        )
    )

    if imagem_url:

        imagem = baixar_imagem_url(
            imagem_url
        )

        if imagem:

            return (
                imagem,
                imagem_url,
            )

    # ------------------------------------------------------------------------
    # Fallback: imagem do catálogo,
    # mas SOMENTE se passar pela validação.
    # ------------------------------------------------------------------------

    image_url_catalogo = device.get(
        "image_url"
    )

    if imagem_eh_valida(
        image_url_catalogo
    ):

        imagem = baixar_imagem_url(
            image_url_catalogo
        )

        if imagem:

            return (
                imagem,
                image_url_catalogo,
            )

    return (
        None,
        None,
    )


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
        obter_imagem_device(
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
                "uma imagem válida do aparelho."
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

    # ------------------------------------------------------------------------
    # 1. SUPABASE
    # ------------------------------------------------------------------------

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

        # --------------------------------------------------------------------
        # Se o Supabase tiver guardado uma imagem ruim,
        # NÃO vamos continuar usando ela.
        #
        # Reprocessamos o aparelho.
        # --------------------------------------------------------------------

    # ------------------------------------------------------------------------
    # 2. GSMARENA
    # ------------------------------------------------------------------------

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
        )
        .decode("ascii")
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

    slug = BRAND_CATALOGS[
        brand
    ]

    paginas = []

    for pagina in range(
        1,
        8,
    ):

        url = gerar_url_catalogo(
            slug,
            pagina,
        )

        html = baixar_html(
            url
        )

        dispositivos = []

        if html:

            dispositivos = (
                extrair_dispositivos_catalogo(
                    html
                )
            )

        encontrado = (
            encontrar_no_catalogo(
                modelo,
                dispositivos,
            )
        )

        paginas.append(
            {
                "pagina": pagina,
                "url": url,
                "dispositivos": len(
                    dispositivos
                ),
                "encontrou_modelo": bool(
                    encontrado
                ),
            }
        )

        if encontrado:

            break

        if (
            pagina > 1
            and len(dispositivos) == 0
        ):

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

    resultado[
        "catalogo"
    ] = bool(
        dispositivos
    )

    resultado[
        "total_dispositivos"
    ] = len(
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
            "Modelo exato não encontrado "
            "no catálogo."
        )

        return jsonify(
            resultado
        )

    resultado[
        "encontrado"
    ] = {
        "name": encontrado.get(
            "name"
        ),
        "url": encontrado.get(
            "url"
        ),
        "image_url_catalogo": encontrado.get(
            "image_url"
        ),
    }

    imagem, imagem_url = (
        obter_imagem_device(
            encontrado
        )
    )

    resultado[
        "imagem"
    ] = bool(
        imagem
    )

    resultado[
        "imagem_bytes"
    ] = (
        len(imagem)
        if imagem
        else 0
    )

    resultado[
        "imagem_url_final"
    ] = imagem_url

    return jsonify(
        resultado
    )


# ============================================================================
# DEBUG - LISTAR MODELOS
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
        total=len(
            dispositivos
        ),
        modelos=dispositivos,
    )


# ============================================================================
# DEBUG - TESTAR IMAGEM DE UM MODELO
# ============================================================================

@app.get("/api/debug-imagem")
def api_debug_imagem():

    modelo = (
        request.args.get(
            "modelo"
        )
        or "iPhone 12"
    ).strip()

    brand = identificar_fabricante(
        modelo
    )

    if not brand:

        return jsonify(
            ok=False,
            erro="Fabricante não identificado.",
        )

    dispositivos = (
        baixar_catalogo_marca(
            brand
        )
    )

    encontrado = (
        encontrar_no_catalogo(
            modelo,
            dispositivos,
        )
    )

    if not encontrado:

        return jsonify(
            ok=False,
            erro="Modelo não encontrado.",
        )

    pagina = (
        encontrar_imagem_pagina(
            encontrado["url"]
        )
    )

    imagem = None

    if pagina:

        imagem = baixar_imagem_url(
            pagina
        )

    return jsonify(
        ok=True,
        modelo=modelo,
        fabricante=brand,
        aparelho=encontrado,
        imagem_url_catalogo=encontrado.get(
            "image_url"
        ),
        imagem_url_pagina=pagina,
        imagem_valida=bool(imagem),
        imagem_bytes=(
            len(imagem)
            if imagem
            else 0
        ),
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
