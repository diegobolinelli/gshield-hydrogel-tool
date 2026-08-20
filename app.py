import base64
import io
import os
import re
import time
from urllib.parse import urljoin, urlparse, unquote

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

SUPABASE_TABLE = "devices"


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
# GSMARENA
# ============================================================================

GSMARENA_BASE = "https://www.gsmarena.com"


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
    "nothing": "nothing-phones-126.php",
    "lenovo": "lenovo-phones-73.php",
    "blackview": "blackview-phones-116.php",
    "tcl": "tcl-phones-123.php",
    "meizu": "meizu-phones-74.php",
    "alcatel": "alcatel-phones-5.php",
}


# Quantas páginas no máximo serão consultadas
# para uma marca.
#
# Isso evita ficar preso caso o site altere
# sua estrutura.
MAX_CATALOG_PAGES = 20


# ============================================================================
# REGEX
# ============================================================================

DEVICE_LINK_RE = re.compile(
    r'href=["\']([^"\']+\.php)["\']',
    re.IGNORECASE,
)

IMAGE_RE = re.compile(
    r'<img[^>]+(?:src|data-src|data-original)=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

SPECS_PHOTO_RE = re.compile(
    r'class=["\'][^"\']*specs-photo-main[^"\']*["\'][^>]*>'
    r'.{0,5000}?'
    r'<img[^>]+(?:src|data-src|data-original)=["\']([^"\']+)["\']',
    re.IGNORECASE | re.DOTALL,
)

FDN_IMAGE_RE = re.compile(
    r'https?://fdn[0-9]+\.gsmarena\.com/[^"\'>\s]+',
    re.IGNORECASE,
)

OG_IMAGE_RE_1 = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

OG_IMAGE_RE_2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    re.IGNORECASE,
)


# ============================================================================
# CACHE DE MARCAS
# ============================================================================

BRAND_MEMORY_CACHE = {}

BRAND_CACHE_TTL = 60 * 60 * 6


# ============================================================================
# NORMALIZAÇÃO
# ============================================================================

def normalizar_modelo(modelo: str):

    modelo = (
        modelo or ""
    ).strip().lower()

    modelo = unquote(modelo)

    modelo = modelo.replace(
        "&",
        " and ",
    )

    modelo = re.sub(
        r"[\-_]+",
        " ",
        modelo,
    )

    modelo = re.sub(
        r"[^\w\s\+\.]",
        " ",
        modelo,
    )

    modelo = re.sub(
        r"\s+",
        " ",
        modelo,
    )

    return modelo.strip()


def normalizar_nome_comparacao(nome: str):

    nome = normalizar_modelo(
        nome
    )

    for brand in BRAND_CATALOGS:

        nome = re.sub(
            rf"^{re.escape(brand)}\s+",
            "",
            nome,
        )

    return nome.strip()


# ============================================================================
# NOME A PARTIR DA URL
# ============================================================================

def nome_a_partir_da_url(url: str):

    if not url:
        return ""

    try:

        path = urlparse(
            url
        ).path

    except Exception:

        return ""

    filename = path.rsplit(
        "/",
        1,
    )[-1]

    filename = re.sub(
        r"\.php$",
        "",
        filename,
        flags=re.IGNORECASE,
    )

    filename = re.sub(
        r"-\d+$",
        "",
        filename,
    )

    filename = filename.replace(
        "_",
        " ",
    )

    filename = re.sub(
        r"\s+",
        " ",
        filename,
    )

    return filename.strip()


# ============================================================================
# FABRICANTE
# ============================================================================

def identificar_fabricante(modelo: str):

    texto = normalizar_modelo(
        modelo
    )

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
        r"^(galaxy|sm-)",
        texto,
    ):
        return "samsung"

    if re.match(
        r"^(redmi|poco|mi\s)",
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
        r"^oneplus\b",
        texto,
    ):
        return "oneplus"

    if re.match(
        r"^oppo\b",
        texto,
    ):
        return "oppo"

    if re.match(
        r"^realme\b",
        texto,
    ):
        return "realme"

    if re.match(
        r"^vivo\b",
        texto,
    ):
        return "vivo"

    if re.match(
        r"^honor\b",
        texto,
    ):
        return "honor"

    if re.match(
        r"^huawei\b",
        texto,
    ):
        return "huawei"

    if re.match(
        r"^asus\b",
        texto,
    ):
        return "asus"

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

    chave = normalizar_modelo(
        modelo
    )

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

        response = requests.get(
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

        response = requests.post(
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
# URL GSMARENA
# ============================================================================

def eh_url_gsmarena(url: str):

    if not url:
        return False

    try:

        parsed = urlparse(
            url
        )

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

    return bool(
        re.search(
            r"-\d+\.php$",
            parsed.path,
            re.IGNORECASE,
        )
    )


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
# IMAGEM VÁLIDA
# ============================================================================

def imagem_url_parece_valida(
    image_url: str,
):

    if not image_url:
        return False

    lower = image_url.lower()

    termos_invalidos = (
        "logo",
        "fallback",
        "favicon",
        "icon",
        "sprite",
        "flag",
        "avatar",
        "placeholder",
        "default",
    )

    if any(
        termo in lower
        for termo in termos_invalidos
    ):
        return False

    if "gsmarena.com" in lower:

        if (
            "fdn1." in lower
            or "fdn2." in lower
            or "/vv/" in lower
        ):
            return True

    return True


# ============================================================================
# EXTRAIR DISPOSITIVOS DE UMA PÁGINA
# ============================================================================

def extrair_dispositivos_catalogo(
    html: str,
):

    dispositivos = []

    links = DEVICE_LINK_RE.findall(
        html
    )

    vistos = set()

    for href in links:

        url = url_absoluta(
            href
        )

        if not eh_url_gsmarena(
            url
        ):
            continue

        if url in vistos:
            continue

        vistos.add(
            url
        )

        nome = ""

        # --------------------------------------------------------------------
        # Tenta encontrar o bloco <li>.
        # --------------------------------------------------------------------

        bloco_match = re.search(
            rf"<li[^>]*>.*?{re.escape(href)}.*?</li>",
            html,
            re.IGNORECASE | re.DOTALL,
        )

        bloco = (
            bloco_match.group(0)
            if bloco_match
            else ""
        )

        if bloco:

            # Primeiro tenta title.
            title_match = re.search(
                r'<a[^>]+title=["\']([^"\']+)["\']',
                bloco,
                re.IGNORECASE,
            )

            if title_match:

                nome = title_match.group(
                    1
                ).strip()

            # Depois tenta texto do link.
            if not nome:

                link_match = re.search(
                    rf'<a[^>]+href=["\']'
                    rf'{re.escape(href)}'
                    rf'["\'][^>]*>'
                    rf'(.*?)</a>',
                    bloco,
                    re.IGNORECASE | re.DOTALL,
                )

                if link_match:

                    nome = re.sub(
                        r"<[^>]+>",
                        " ",
                        link_match.group(
                            1
                        ),
                    )

                    nome = re.sub(
                        r"\s+",
                        " ",
                        nome,
                    ).strip()

        # --------------------------------------------------------------------
        # Fallback pelo slug.
        # --------------------------------------------------------------------

        if not nome:

            nome = nome_a_partir_da_url(
                url
            )

        image_url = None

        if bloco:

            imagens = IMAGE_RE.findall(
                bloco
            )

            for imagem in imagens:

                imagem = url_absoluta(
                    imagem
                )

                if imagem_url_parece_valida(
                    imagem
                ):

                    image_url = imagem
                    break

        dispositivos.append(
            {
                "name": nome,
                "url": url,
                "image_url": image_url,
            }
        )

    return dispositivos


# ============================================================================
# BAIXAR UMA PÁGINA DO CATÁLOGO
# ============================================================================

def baixar_pagina_catalogo(
    brand: str,
    pagina: int,
):

    slug = BRAND_CATALOGS.get(
        brand
    )

    if not slug:
        return []

    if pagina <= 1:

        url = (
            f"{GSMARENA_BASE}/"
            f"{slug}"
        )

    else:

        url = (
            f"{GSMARENA_BASE}/"
            f"{slug}"
            f"?p={pagina}"
        )

    try:

        response = SESSION.get(
            url,
            headers=GSMARENA_HEADERS,
            timeout=20,
        )

    except requests.exceptions.RequestException:

        return []

    if response.status_code != 200:

        return []

    html = response.text

    if not html:
        return []

    return extrair_dispositivos_catalogo(
        html
    )


# ============================================================================
# CARREGAR CATÁLOGO
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

        timestamp, devices = cache

        if (
            agora - timestamp
            < BRAND_CACHE_TTL
        ):

            return devices

    todos = []

    urls_vistas = set()

    # ------------------------------------------------------------------------
    # Percorre as páginas.
    # ------------------------------------------------------------------------

    for pagina in range(
        1,
        MAX_CATALOG_PAGES + 1,
    ):

        dispositivos = baixar_pagina_catalogo(
            brand,
            pagina,
        )

        if not dispositivos:
            break

        novos = 0

        for device in dispositivos:

            url = device.get(
                "url"
            )

            if not url:
                continue

            if url in urls_vistas:
                continue

            urls_vistas.add(
                url
            )

            todos.append(
                device
            )

            novos += 1

        # --------------------------------------------------------------------
        # Se a página não trouxe nenhum
        # aparelho novo, paramos.
        # --------------------------------------------------------------------

        if novos == 0:
            break

        # --------------------------------------------------------------------
        # Se encontrou uma quantidade muito
        # pequena, provavelmente chegamos
        # ao final.
        # --------------------------------------------------------------------

        if len(dispositivos) < 5:
            break

    BRAND_MEMORY_CACHE[
        brand
    ] = (
        agora,
        todos,
    )

    return todos


# ============================================================================
# MATCH EXATO
# ============================================================================

def encontrar_match_exato(
    procurado: str,
    dispositivos: list,
):

    for device in dispositivos:

        nome = normalizar_nome_comparacao(
            device.get(
                "name",
                "",
            )
        )

        if nome == procurado:
            return device

    for device in dispositivos:

        nome_url = normalizar_nome_comparacao(
            nome_a_partir_da_url(
                device.get(
                    "url",
                    "",
                )
            )
        )

        if nome_url == procurado:
            return device

    return None


# ============================================================================
# MATCH POR TOKENS
# ============================================================================

def encontrar_match_tokens(
    procurado: str,
    dispositivos: list,
):

    procurado_tokens = set(
        procurado.split()
    )

    if not procurado_tokens:
        return None

    variantes = {
        "pro",
        "max",
        "plus",
        "ultra",
        "mini",
        "lite",
        "edge",
        "fe",
        "play",
        "zoom",
        "neo",
        "prime",
        "se",
        "air",
    }

    candidatos = []

    for device in dispositivos:

        nomes = [
            normalizar_nome_comparacao(
                device.get(
                    "name",
                    "",
                )
            ),
            normalizar_nome_comparacao(
                nome_a_partir_da_url(
                    device.get(
                        "url",
                        "",
                    )
                )
            ),
        ]

        for nome in nomes:

            if not nome:
                continue

            tokens = set(
                nome.split()
            )

            if not procurado_tokens.issubset(
                tokens
            ):
                continue

            extras = (
                tokens
                - procurado_tokens
            )

            # Se houver uma variante,
            # não tratamos como o mesmo aparelho.
            if any(
                extra in variantes
                for extra in extras
            ):
                continue

            candidatos.append(
                (
                    len(extras),
                    len(tokens),
                    device,
                )
            )

            break

    if not candidatos:
        return None

    candidatos.sort(
        key=lambda item: (
            item[0],
            item[1],
        )
    )

    return candidatos[0][2]


# ============================================================================
# BUSCA NO CATÁLOGO
# ============================================================================

def encontrar_no_catalogo(
    modelo: str,
    dispositivos: list,
):

    procurado = normalizar_nome_comparacao(
        modelo
    )

    if not procurado:
        return None

    # Primeiro: EXATO.
    encontrado = encontrar_match_exato(
        procurado,
        dispositivos,
    )

    if encontrado:
        return encontrado

    # Segundo: tokens sem variantes.
    return encontrar_match_tokens(
        procurado,
        dispositivos,
    )


# ============================================================================
# IMAGEM DA PÁGINA
# ============================================================================

def extrair_imagem_da_pagina(
    html: str,
):

    if not html:
        return None

    match = SPECS_PHOTO_RE.search(
        html
    )

    if match:

        imagem = url_absoluta(
            match.group(1)
        )

        if imagem_url_parece_valida(
            imagem
        ):
            return imagem

    match = OG_IMAGE_RE_1.search(
        html
    )

    if not match:

        match = OG_IMAGE_RE_2.search(
            html
        )

    if match:

        imagem = url_absoluta(
            match.group(1)
        )

        if imagem_url_parece_valida(
            imagem
        ):
            return imagem

    matches = FDN_IMAGE_RE.findall(
        html
    )

    for imagem in matches:

        imagem = imagem.rstrip(
            ".,);"
        )

        if imagem_url_parece_valida(
            imagem
        ):
            return imagem

    return None


def obter_imagem_do_aparelho(
    device_url: str,
):

    if not device_url:
        return None

    if not eh_url_gsmarena(
        device_url
    ):
        return None

    try:

        response = SESSION.get(
            device_url,
            headers=GSMARENA_HEADERS,
            timeout=20,
        )

    except requests.exceptions.RequestException:

        return None

    if response.status_code != 200:
        return None

    return extrair_imagem_da_pagina(
        response.text
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

    dispositivos = baixar_catalogo_marca(
        brand
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

    encontrado = encontrar_no_catalogo(
        modelo,
        dispositivos,
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

    nome = (
        encontrado.get(
            "name"
        )
        or nome_a_partir_da_url(
            encontrado.get(
                "url",
                "",
            )
        )
    )

    device_url = encontrado.get(
        "url"
    )

    image_url = encontrado.get(
        "image_url"
    )

    # ------------------------------------------------------------------------
    # Valida imagem do catálogo.
    # ------------------------------------------------------------------------

    if not imagem_url_parece_valida(
        image_url
    ):

        image_url = None

    # ------------------------------------------------------------------------
    # Busca imagem na página individual.
    # ------------------------------------------------------------------------

    if not image_url:

        image_url = obter_imagem_do_aparelho(
            device_url
        )

    if not image_url:

        return None, {
            "etapa": "imagem",
            "fabricante": brand,
            "modelo": nome,
            "url": device_url,
            "motivo": (
                "Modelo encontrado, "
                "mas não consegui localizar "
                "uma imagem válida."
            ),
        }

    # ------------------------------------------------------------------------
    # Salva no Supabase usando o modelo
    # pesquisado como chave.
    # ------------------------------------------------------------------------

    normalizado = normalizar_modelo(
        modelo
    )

    salvo = supabase_salvar_modelo(
        model_name=nome,
        model_normalized=normalizado,
        device_url=device_url,
        image_url=image_url,
    )

    return {
        "model_name": nome,
        "model_normalized": normalizado,
        "device_url": device_url,
        "image_url": image_url,
        "fabricante": brand,
        "salvo": salvo,
    }, None


# ============================================================================
# DOWNLOAD DE IMAGEM
# ============================================================================

def baixar_imagem_url(
    image_url: str,
):

    if not image_url:
        return None

    if not imagem_url_parece_valida(
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

    try:

        image = Image.open(
            io.BytesIO(
                response.content
            )
        )

        image.verify()

    except Exception:

        return None

    try:

        image = Image.open(
            io.BytesIO(
                response.content
            )
        )

        largura, altura = image.size

        if largura < 200 or altura < 200:
            return None

    except Exception:

        return None

    return response.content


# ============================================================================
# OBTÉM MODELO + IMAGEM
# ============================================================================

def obter_modelo_e_imagem(
    modelo: str,
):

    # ------------------------------------------------------------------------
    # 1. SUPABASE
    # ------------------------------------------------------------------------

    existente = supabase_buscar_modelo(
        modelo
    )

    if existente:

        imagem = baixar_imagem_url(
            existente.get(
                "image_url"
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

    # ------------------------------------------------------------------------
    # 2. GSMARENA
    # ------------------------------------------------------------------------

    encontrado, erro = buscar_modelo_novo(
        modelo
    )

    if not encontrado:

        return None, erro

    imagem = baixar_imagem_url(
        encontrado[
            "image_url"
        ]
    )

    if not imagem:

        return None, {
            "etapa": "download",
            "motivo": (
                "Modelo encontrado, "
                "mas não consegui baixar "
                "uma imagem válida."
            ),
        }

    return {
        "imagem": imagem,
        "modelo": encontrado[
            "model_name"
        ],
        "url": encontrado[
            "device_url"
        ],
        "origem": "gsmarena_catalogo",
        "salvo": encontrado[
            "salvo"
        ],
    }, None


# ============================================================================
# COMPOSIÇÃO
# ============================================================================

def compose_image(
    phone_bytes: bytes,
):

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
        .decode(
            "ascii"
        )
    )

    return jsonify(
        ok=True,
        imagem_base64=imagem_b64,
        url_produto=resultado[
            "url"
        ],
        modelo=resultado[
            "modelo"
        ],
        origem=resultado[
            "origem"
        ],
    )


# ============================================================================
# API — COMPOR
# ============================================================================

@app.post("/api/compor")
def api_compor():

    phone_bytes = None

    if "foto" in request.files:

        phone_bytes = (
            request.files[
                "foto"
            ].read()
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
            motivo="Nenhuma imagem de celular recebida.",
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
# DEBUG — SUPABASE
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

        response = requests.get(
            url,
            headers=supabase_headers(),
            params={
                "select": "id",
                "limit": "1",
            },
            timeout=10,
        )

        return jsonify(
            ok=(
                response.status_code == 200
            ),
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
# DEBUG — GSMARENA
# ============================================================================

@app.get("/api/debug-gsmarena")
def api_debug_gsmarena():

    modelo = (
        request.args.get(
            "modelo"
        )
        or "iPhone 16 Pro Max"
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

    dispositivos = baixar_catalogo_marca(
        brand
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

    encontrado = encontrar_no_catalogo(
        modelo,
        dispositivos,
    )

    if not encontrado:

        resultado["erro"] = (
            "Modelo exato não encontrado "
            "no catálogo."
        )

        return jsonify(
            resultado
        )

    image_url = encontrado.get(
        "image_url"
    )

    if not imagem_url_parece_valida(
        image_url
    ):

        image_url = None

    if not image_url:

        image_url = obter_imagem_do_aparelho(
            encontrado[
                "url"
            ]
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
        "image_url": image_url,
    }

    if not image_url:

        resultado["erro"] = (
            "Modelo encontrado, "
            "mas nenhuma imagem válida "
            "foi localizada."
        )

        return jsonify(
            resultado
        )

    imagem = baixar_imagem_url(
        image_url
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

    return jsonify(
        resultado
    )


# ============================================================================
# DEBUG — LISTAR MODELOS
# ============================================================================

@app.get("/api/debug-modelos")
def api_debug_modelos():

    brand = (
        request.args.get(
            "marca"
        )
        or "apple"
    ).strip().lower()

    dispositivos = baixar_catalogo_marca(
        brand
    )

    return jsonify(
        ok=True,
        marca=brand,
        total=len(
            dispositivos
        ),
        modelos=[
            {
                "name": d.get(
                    "name"
                ),
                "url": d.get(
                    "url"
                ),
                "slug": nome_a_partir_da_url(
                    d.get(
                        "url",
                        "",
                    )
                ),
                "image_url": d.get(
                    "image_url"
                ),
            }
            for d in dispositivos
        ],
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
