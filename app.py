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

TEMPLATE_PATH = "static/template.jpg"
CANVAS_SIZE = (2000, 2000)
PHONE_BOX = (70, 220, 900, 1780)

GSMARENA_BASE = "https://www.gsmarena.com"
SUPABASE_TABLE = "devices"


# ============================================================================
# SUPABASE
# ============================================================================

SUPABASE_URL = (
    os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
)

SUPABASE_SECRET_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)


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


def supabase_buscar_modelo(modelo):
    if not SUPABASE_URL:
        return None

    chave = normalizar_modelo(modelo)
    if not chave:
        return None

    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"

    try:
        response = SESSION.get(
            url,
            headers=supabase_headers(),
            params={
                "model_normalized": f"eq.{chave}",
                "select": "*",
                "limit": "1",
            },
            timeout=8,
        )
    except requests.exceptions.RequestException:
        return None

    if response.status_code != 200:
        return None

    try:
        data = response.json()
    except ValueError:
        return None

    return data[0] if data else None


def supabase_salvar_modelo(
    model_name,
    model_normalized,
    device_url,
    image_url,
):
    if not SUPABASE_URL:
        return False

    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"

    payload = {
        "model_name": model_name,
        "model_normalized": model_normalized,
        "device_url": device_url,
        "image_url": image_url,
    }

    headers = supabase_headers()
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"

    try:
        response = SESSION.post(
            url,
            headers=headers,
            json=payload,
            timeout=8,
        )
    except requests.exceptions.RequestException:
        return False

    return response.status_code in {200, 201, 204}


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
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://www.gsmarena.com/",
}

SESSION = requests.Session()
SESSION.headers.update(GSMARENA_HEADERS)


# ============================================================================
# FABRICANTES
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
#
# IMPORTANTE:
# O cache guarda somente a sessão atual da função.
# A memória permanente é o Supabase.
# ============================================================================

BRAND_MEMORY_CACHE = {}
BRAND_CACHE_TTL = 60 * 60 * 6

# Limite de segurança.
# A busca para imediatamente quando encontra o aparelho.
MAX_CATALOG_PAGES = 12


# ============================================================================
# NORMALIZAÇÃO
# ============================================================================

def normalizar_modelo(modelo):
    texto = (modelo or "").strip().lower()
    texto = texto.replace("_", " ")
    texto = texto.replace("-", " ")
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def remover_acentos(texto):
    texto = unicodedata.normalize("NFD", texto or "")
    return "".join(
        char
        for char in texto
        if unicodedata.category(char) != "Mn"
    )


def normalizar_comparacao(texto):
    return remover_acentos(
        normalizar_modelo(texto)
    ).strip()


def normalizar_nome_modelo(texto):
    texto = normalizar_comparacao(texto)

    # Remove fabricante quando estiver no começo.
    for brand in BRAND_CATALOGS:
        if texto.startswith(brand + " "):
            texto = texto[len(brand) + 1:]

    texto = re.sub(r"\s+5g$", "", texto).strip()
    texto = re.sub(r"\s+4g$", "", texto).strip()

    return texto


# ============================================================================
# FABRICANTE
# ============================================================================

def identificar_fabricante(modelo):
    texto = normalizar_comparacao(modelo)

    for brand in BRAND_CATALOGS:
        if re.search(rf"\b{re.escape(brand)}\b", texto):
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

    if re.match(r"^oneplus\b", texto):
        return "oneplus"

    if re.match(r"^oppo\b", texto):
        return "oppo"

    if re.match(r"^realme\b", texto):
        return "realme"

    if re.match(r"^vivo\b", texto):
        return "vivo"

    if re.match(r"^honor\b", texto):
        return "honor"

    if re.match(r"^huawei\b", texto):
        return "huawei"

    if re.match(r"^asus\b", texto):
        return "asus"

    if re.match(r"^xperia\b", texto):
        return "sony"

    if re.match(r"^nokia\b", texto):
        return "nokia"

    if re.match(r"^nothing\b", texto):
        return "nothing"

    return None


# ============================================================================
# GSMARENA - URL
# ============================================================================

def url_absoluta(url):
    if not url:
        return None

    url = url.strip()

    if url.startswith("//"):
        return "https:" + url

    if url.startswith("/"):
        return urljoin(GSMARENA_BASE, url)

    if url.startswith("http://") or url.startswith("https://"):
        return url

    return urljoin(GSMARENA_BASE + "/", url)


def eh_url_aparelho_gsmarena(url):
    if not url:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    hostname = (parsed.hostname or "").lower()

    if hostname not in {"gsmarena.com", "www.gsmarena.com"}:
        return False

    caminho = parsed.path or ""

    if not re.search(r"-\d+\.php$", caminho, re.IGNORECASE):
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

    return not any(item in caminho_lower for item in proibidos)


def baixar_html(url):
    try:
        response = SESSION.get(
            url,
            headers=GSMARENA_HEADERS,
            timeout=8,
        )
    except requests.exceptions.RequestException:
        return None

    if response.status_code != 200 or not response.text:
        return None

    return response.text


# ============================================================================
# CATÁLOGO - EXTRAÇÃO
# ============================================================================

def limpar_nome_aparelho(texto):
    texto = re.sub(r"<[^>]+>", " ", texto or "")
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def extrair_dispositivos_catalogo(html):
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

        nome = limpar_nome_aparelho(conteudo)

        if not nome:
            continue

        if "show only the devices" in nome.lower():
            continue

        if nome.lower().endswith(" review"):
            continue

        imagens = re.findall(
            r'(?:src|data-src|data-original|data-lazy-src)=["\']([^"\']+)["\']',
            conteudo,
            re.IGNORECASE,
        )

        image_url = url_absoluta(imagens[0]) if imagens else None

        dispositivos.append({
            "name": nome,
            "url": url,
            "image_url": image_url,
        })

    resultado = []
    vistos = set()

    for device in dispositivos:
        if device["url"] in vistos:
            continue

        vistos.add(device["url"])
        resultado.append(device)

    return resultado


# ============================================================================
# PAGINAÇÃO INTELIGENTE
#
# Em vez de gerar 150 URLs e testar todas:
#
# 1. carrega a página do fabricante;
# 2. extrai os aparelhos;
# 3. descobre os links reais de paginação presentes no HTML;
# 4. segue somente a próxima página;
# 5. PARA assim que encontra o aparelho.
# ============================================================================

def extrair_links_paginacao(html, pagina_atual):
    links = []

    padrao = re.compile(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )

    for match in padrao.finditer(html):
        href = match.group(1)
        conteudo = limpar_nome_aparelho(match.group(2))

        url = url_absoluta(href)

        if not url:
            continue

        caminho = urlparse(url).path.lower()

        # Só aceitamos URLs de paginação do catálogo.
        if "phones-f-" not in caminho:
            continue

        if not re.search(r"-p\d+\.php$", caminho):
            continue

        if url not in links:
            links.append(url)

    # Ordena pelas páginas.
    def numero_pagina(url):
        match = re.search(r"-p(\d+)\.php$", url.lower())
        return int(match.group(1)) if match else 9999

    links.sort(key=numero_pagina)

    # Nunca volta para uma página anterior.
    resultado = []

    for url in links:
        numero = numero_pagina(url)

        if numero > pagina_atual:
            resultado.append(url)

    return resultado


def url_pagina_catalogo_fallback(slug, pagina):
    match = re.match(
        r"^(.+?)-phones-(\d+)\.php$",
        slug,
        re.IGNORECASE,
    )

    if not match:
        return None

    marca = match.group(1)
    codigo = match.group(2)

    return (
        f"{GSMARENA_BASE}/"
        f"{marca}-phones-f-{codigo}-0-p{pagina}.php"
    )


def encontrar_modelo_no_catalogo_gsmarena(modelo, brand):
    slug = BRAND_CATALOGS[brand]

    procurado = normalizar_nome_modelo(modelo)

    # Evita acumular centenas de dispositivos.
    todos = []

    visitadas = set()

    url_atual = f"{GSMARENA_BASE}/{slug}"
    pagina_atual = 1

    for _ in range(MAX_CATALOG_PAGES):
        if url_atual in visitadas:
            break

        visitadas.add(url_atual)

        html = baixar_html(url_atual)

        if not html:
            break

        dispositivos = extrair_dispositivos_catalogo(html)
        todos.extend(dispositivos)

        # Procura imediatamente nesta página.
        encontrado = encontrar_no_catalogo(
            modelo,
            dispositivos,
        )

        if encontrado:
            return encontrado, todos, pagina_atual

        # Descobre a próxima página diretamente do HTML.
        proximas = extrair_links_paginacao(
            html,
            pagina_atual,
        )

        proxima_url = proximas[0] if proximas else None

        # Fallback específico do padrão conhecido.
        if not proxima_url:
            proxima_url = url_pagina_catalogo_fallback(
                slug,
                pagina_atual + 1,
            )

        if not proxima_url or proxima_url in visitadas:
            break

        pagina_atual += 1
        url_atual = proxima_url

        # Pequena pausa para não bombardear o site.
        time.sleep(0.05)

    return None, todos, pagina_atual


# ============================================================================
# ENCONTRAR MODELO
# ============================================================================

def encontrar_no_catalogo(modelo, dispositivos):
    procurado = normalizar_nome_modelo(modelo)

    if not procurado:
        return None

    # 1. Correspondência exata.
    for device in dispositivos:
        nome = normalizar_nome_modelo(device.get("name", ""))

        if nome == procurado:
            return device

    # 2. Correspondência sem 5G.
    procurado_sem_5g = re.sub(
        r"\s+5g$",
        "",
        procurado,
    ).strip()

    for device in dispositivos:
        nome = normalizar_nome_modelo(device.get("name", ""))
        nome_sem_5g = re.sub(
            r"\s+5g$",
            "",
            nome,
        ).strip()

        if nome_sem_5g == procurado_sem_5g:
            return device

    # 3. Correspondência forte por tokens.
    procurado_tokens = set(procurado.split())

    melhor = None
    melhor_score = 0

    for device in dispositivos:
        nome = normalizar_nome_modelo(device.get("name", ""))
        tokens = set(nome.split())

        if not tokens:
            continue

        intersecao = procurado_tokens & tokens

        if not intersecao:
            continue

        score = len(intersecao) / max(
            len(procurado_tokens),
            len(tokens),
        )

        if score < 0.85:
            continue

        if score > melhor_score:
            melhor_score = score
            melhor = device

    return melhor


# ============================================================================
# IMAGENS
# ============================================================================

def imagem_parece_placeholder(image_url):
    if not image_url:
        return True

    texto = image_url.lower()

    bloqueadas = (
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

    return any(palavra in texto for palavra in bloqueadas)


def imagem_eh_url_valida(image_url):
    if not image_url:
        return False

    if imagem_parece_placeholder(image_url):
        return False

    return image_url.startswith(("http://", "https://"))


def baixar_imagem_url(image_url):
    if not imagem_eh_url_valida(image_url):
        return None

    try:
        response = SESSION.get(
            image_url,
            headers=GSMARENA_HEADERS,
            timeout=8,
        )
    except requests.exceptions.RequestException:
        return None

    if response.status_code != 200 or not response.content:
        return None

    content_type = response.headers.get(
        "Content-Type",
        "",
    ).lower()

    if content_type and not content_type.startswith("image/"):
        return None

    try:
        image = Image.open(io.BytesIO(response.content))
        width, height = image.size
        image.verify()
    except Exception:
        return None

    if width < 150 or height < 150:
        return None

    return response.content


def extrair_urls_imagem_do_html(html):
    encontrados = []

    # OG image / Twitter image.
    padroes_meta = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    ]

    for padrao in padroes_meta:
        encontrados.extend(
            re.findall(
                padrao,
                html,
                re.IGNORECASE,
            )
        )

    # Prioridade para imagens do próprio GSMArena.
    encontrados.extend(
        re.findall(
            r'["\']([^"\']*bigpic[^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
    )

    encontrados.extend(
        re.findall(
            r'["\'](https?://fdn\d*\.gsmarena\.com/[^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
    )

    encontrados.extend(
        re.findall(
            r'(?:src|data-src|data-original|data-lazy-src)=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
    )

    resultado = []
    vistos = set()

    for url in encontrados:
        url = url_absoluta(url)

        if not imagem_eh_url_valida(url):
            continue

        if url in vistos:
            continue

        vistos.add(url)
        resultado.append(url)

    return resultado


def encontrar_imagem_pagina(device_url):
    html = baixar_html(device_url)

    if not html:
        return None

    candidatos = extrair_urls_imagem_do_html(html)

    # bigpic primeiro.
    candidatos.sort(
        key=lambda url: (
            0 if "bigpic" in url.lower() else 1,
            0 if "gsmarena.com" in url.lower() else 1,
        )
    )

    for image_url in candidatos:
        imagem = baixar_imagem_url(image_url)

        if imagem:
            return image_url

    return None


def obter_imagem_device(device):
    device_url = device.get("url")

    if not device_url:
        return None, None

    # Primeiro a ficha individual.
    imagem_url = encontrar_imagem_pagina(device_url)

    if imagem_url:
        imagem = baixar_imagem_url(imagem_url)

        if imagem:
            return imagem, imagem_url

    # Depois a imagem do catálogo, somente se válida.
    image_url_catalogo = device.get("image_url")

    if imagem_eh_url_valida(image_url_catalogo):
        imagem = baixar_imagem_url(image_url_catalogo)

        if imagem:
            return imagem, image_url_catalogo

    return None, None


# ============================================================================
# BUSCAR MODELO NOVO
# ============================================================================

def buscar_modelo_novo(modelo):
    brand = identificar_fabricante(modelo)

    if not brand:
        return None, {
            "etapa": "fabricante",
            "motivo": "Não consegui identificar o fabricante.",
        }

    encontrado, dispositivos, paginas = (
        encontrar_modelo_no_catalogo_gsmarena(
            modelo,
            brand,
        )
    )

    if not encontrado:
        return None, {
            "etapa": "catalogo",
            "fabricante": brand,
            "total_dispositivos": len(dispositivos),
            "paginas_consultadas": paginas,
            "motivo": (
                "Modelo não encontrado no catálogo "
                "consultado."
            ),
        }

    imagem, imagem_url = obter_imagem_device(encontrado)

    if not imagem:
        return None, {
            "etapa": "imagem",
            "fabricante": brand,
            "modelo": encontrado.get("name"),
            "device_url": encontrado.get("url"),
            "motivo": (
                "Modelo encontrado, mas não consegui "
                "obter uma imagem válida do aparelho."
            ),
        }

    nome = encontrado.get("name") or modelo
    normalizado = normalizar_modelo(modelo)
    device_url = encontrado.get("url")

    salvo = supabase_salvar_modelo(
        model_name=nome,
        model_normalized=normalizado,
        device_url=device_url,
        image_url=imagem_url,
    )

    return {
        "imagem": imagem,
        "model_name": nome,
        "model_normalized": normalizado,
        "device_url": device_url,
        "image_url": imagem_url,
        "fabricante": brand,
        "salvo": salvo,
        "origem": "gsmarena",
    }, None


# ============================================================================
# FLUXO PRINCIPAL
# ============================================================================

def obter_modelo_e_imagem(modelo):
    # 1. Supabase.
    existente = supabase_buscar_modelo(modelo)

    if existente:
        imagem = baixar_imagem_url(
            existente.get("image_url")
        )

        if imagem:
            return {
                "imagem": imagem,
                "modelo": existente.get("model_name"),
                "url": existente.get("device_url"),
                "origem": "supabase",
            }, None

    # 2. GSMArena.
    return buscar_modelo_novo(modelo)


# ============================================================================
# COMPOSIÇÃO
# ============================================================================

def compose_image(phone_bytes):
    template = Image.open(TEMPLATE_PATH).convert("RGB")

    if template.size != CANVAS_SIZE:
        template = template.resize(
            CANVAS_SIZE,
            Image.LANCZOS,
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

    scale = min(
        box_w / phone.width,
        box_h / phone.height,
    )

    new_w = max(1, int(phone.width * scale))
    new_h = max(1, int(phone.height * scale))

    phone_resized = phone.resize(
        (new_w, new_h),
        Image.LANCZOS,
    )

    paste_x = x0 + (box_w - new_w) // 2
    paste_y = y0 + (box_h - new_h) // 2

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

    try:
        resultado, erro = obter_modelo_e_imagem(modelo)
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

    imagem_b64 = base64.b64encode(
        resultado["imagem"]
    ).decode("ascii")

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
        origem=resultado.get("origem"),
    )


# ============================================================================
# API - COMPOR
# ============================================================================

@app.post("/api/compor")
def api_compor():
    phone_bytes = None

    if "foto" in request.files:
        phone_bytes = request.files["foto"].read()

    elif request.is_json:
        data = request.get_json(silent=True) or {}
        b64 = data.get("imagem_base64")

        if b64:
            try:
                phone_bytes = base64.b64decode(b64)
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
        resultado = compose_image(phone_bytes)
    except Exception as exc:
        return jsonify(
            ok=False,
            motivo=f"Não consegui compor a imagem: {exc}",
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
            erro="SUPABASE_SERVICE_ROLE_KEY não configurada.",
        )

    try:
        url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"

        response = SESSION.get(
            url,
            headers=supabase_headers(),
            params={
                "select": "id",
                "limit": "1",
            },
            timeout=8,
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
# DEBUG - GSMARENA
# ============================================================================

@app.get("/api/debug-gsmarena")
def api_debug_gsmarena():
    modelo = (
        request.args.get("modelo")
        or "Samsung Galaxy S26"
    ).strip()

    brand = identificar_fabricante(modelo)

    resultado = {
        "ok": True,
        "modelo": modelo,
        "fabricante": brand,
        "catalogo": False,
        "total_dispositivos": 0,
        "encontrado": None,
        "imagem": False,
        "imagem_bytes": 0,
        "imagem_url_final": None,
    }

    if not brand:
        resultado["erro"] = "Fabricante não identificado."
        return jsonify(resultado)

    encontrado, dispositivos, paginas = (
        encontrar_modelo_no_catalogo_gsmarena(
            modelo,
            brand,
        )
    )

    resultado["catalogo"] = bool(dispositivos)
    resultado["total_dispositivos"] = len(dispositivos)
    resultado["paginas_consultadas"] = paginas

    if not encontrado:
        resultado["erro"] = "Modelo não encontrado no catálogo."
        return jsonify(resultado)

    resultado["encontrado"] = {
        "name": encontrado.get("name"),
        "url": encontrado.get("url"),
        "image_url_catalogo": encontrado.get("image_url"),
    }

    imagem, imagem_url = obter_imagem_device(encontrado)

    resultado["imagem"] = bool(imagem)
    resultado["imagem_bytes"] = len(imagem) if imagem else 0
    resultado["imagem_url_final"] = imagem_url

    return jsonify(resultado)


# ============================================================================
# DEBUG - IMAGEM
# ============================================================================

@app.get("/api/debug-imagem")
def api_debug_imagem():
    modelo = (
        request.args.get("modelo")
        or "Samsung Galaxy S26"
    ).strip()

    brand = identificar_fabricante(modelo)

    if not brand:
        return jsonify(
            ok=False,
            erro="Fabricante não identificado.",
        )

    encontrado, dispositivos, paginas = (
        encontrar_modelo_no_catalogo_gsmarena(
            modelo,
            brand,
        )
    )

    if not encontrado:
        return jsonify(
            ok=False,
            erro="Modelo não encontrado.",
            fabricante=brand,
            total=len(dispositivos),
            paginas_consultadas=paginas,
        )

    imagem_url = encontrar_imagem_pagina(
        encontrado["url"]
    )

    imagem = (
        baixar_imagem_url(imagem_url)
        if imagem_url
        else None
    )

    return jsonify(
        ok=True,
        modelo=modelo,
        fabricante=brand,
        aparelho=encontrado,
        imagem_url_catalogo=encontrado.get("image_url"),
        imagem_url_pagina=imagem_url,
        imagem_valida=bool(imagem),
        imagem_bytes=len(imagem) if imagem else 0,
        paginas_consultadas=paginas,
    )


# ============================================================================
# DEBUG - PAGINAÇÃO
# ============================================================================

@app.get("/api/debug-paginacao")
def api_debug_paginacao():
    modelo = (
        request.args.get("modelo")
        or "Samsung Galaxy S26"
    ).strip()

    brand = identificar_fabricante(modelo)

    if not brand:
        return jsonify(
            ok=False,
            erro="Fabricante não identificado.",
        )

    encontrado, dispositivos, paginas = (
        encontrar_modelo_no_catalogo_gsmarena(
            modelo,
            brand,
        )
    )

    return jsonify(
        ok=True,
        modelo=modelo,
        fabricante=brand,
        total_dispositivos=len(dispositivos),
        encontrou_modelo=bool(encontrado),
        encontrado=encontrado,
        paginas_consultadas=paginas,
    )


# ============================================================================
# DEBUG - MODELOS
# ============================================================================

@app.get("/api/debug-modelos")
def api_debug_modelos():
    brand = (
        request.args.get("marca")
        or "samsung"
    ).strip().lower()

    if brand not in BRAND_CATALOGS:
        return jsonify(
            ok=False,
            erro="Marca não cadastrada.",
        )

    # Retorna somente a primeira página.
    # Não deve disparar uma varredura completa.
    slug = BRAND_CATALOGS[brand]
    html = baixar_html(
        f"{GSMARENA_BASE}/{slug}"
    )

    dispositivos = (
        extrair_dispositivos_catalogo(html)
        if html
        else []
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
        supabase_configurada=bool(SUPABASE_URL),
        supabase_secret_configurada=bool(
            SUPABASE_SECRET_KEY
        ),
        fabricantes_disponiveis=len(BRAND_CATALOGS),
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
