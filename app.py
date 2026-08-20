"""
GShield - Gerador de imagem Hydrogel + foto do celular

Fluxo:
  1) /api/buscar?modelo=... -> tenta encontrar automaticamente uma foto do
     celular no tudocelular.com (best-effort, pode falhar/ser bloqueado).
  2) /api/compor -> recebe uma imagem do celular (vinda da busca automática
     OU enviada manualmente pelo usuário) e gera o JPG final, celular ao
     lado da embalagem do hydrogel.

IMPORTANTE (leia antes de mexer no scraping):
  - A busca automática (etapa 1) é uma tentativa razoável, não uma
    garantia. Ela usa a busca HTML do DuckDuckGo (com "site:tudocelular.com")
    para achar a página do modelo, e depois lê a tag <meta property="og:image">
    dessa página.
  - Se algo nessa cadeia quebrar (site mudou o HTML, bloqueou o IP do
    servidor, DuckDuckGo mudou o formato, etc.), o endpoint retorna
    ok:false com um motivo, e a interface cai automaticamente no modo
    manual (a pessoa cola/envia a foto do celular à mão). Esse modo
    manual não depende de scraping nenhum e sempre funciona.
"""

import base64
import io
import re
from urllib.parse import quote, unquote

import requests
from flask import Flask, jsonify, request, send_file, send_from_directory
from PIL import Image

app = Flask(__name__, static_folder="static", static_url_path="")

# ---------------------------------------------------------------------------
# Config de composição de imagem
# ---------------------------------------------------------------------------

TEMPLATE_PATH = "static/template.jpg"
CANVAS_SIZE = (2000, 2000)  # medido a partir do arquivo enviado

# Caixa (x0, y0, x1, y1) onde a foto do celular deve ser encaixada.
# Medido automaticamente a partir do molde: a arte da embalagem ocupa
# aprox. colunas 970-1971 e linhas 256-1781 de um canvas 2000x2000, ou
# seja, a metade esquerda está em branco. Estes valores dão uma margem
# de respiro em volta da foto do celular dentro dessa área em branco.
# Ajuste aqui se o enquadramento não ficar bom.
PHONE_BOX = (70, 220, 900, 1780)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Cabeçalhos simples pra falar com o DuckDuckGo (só isso, sem firulas —
# cabeçalhos "bonitos demais"/incoerentes fizeram o DDG desconfiar numa
# tentativa anterior e devolver uma página vazia).
REQUEST_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

# Cabeçalhos mais "de navegador" pra falar com o tudocelular.com, com um
# Referer condizente com a realidade (a pessoa clicou num resultado do
# DuckDuckGo pra chegar lá).
TUDOCELULAR_HEADERS = {
    "User-Agent": _UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://duckduckgo.com/",
}

OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
TUDOCELULAR_LINK_RE = re.compile(
    r'https?://(?:www\.)?tudocelular\.com/[^\s"\'<>]*fichas-tecnicas[^\s"\'<>]*\.html'
)
# O DuckDuckGo (versão HTML) não linka direto pro site: ele embrulha a URL
# de destino dentro de um link de redirecionamento próprio, tipo
# "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.tudocelular.com%2F...&rut=...".
# Esse regex pega o valor do parâmetro uddg (ainda codificado em URL).
DUCKDUCKGO_UDDG_RE = re.compile(r'uddg=([^&"\']+)')


def compose_image(phone_bytes: bytes) -> Image.Image:
    """Cola a foto do celular (phone_bytes) ao lado do molde da embalagem."""
    template = Image.open(TEMPLATE_PATH).convert("RGB")
    if template.size != CANVAS_SIZE:
        template = template.resize(CANVAS_SIZE)

    phone = Image.open(io.BytesIO(phone_bytes))
    phone = phone.convert("RGBA") if "A" in phone.getbands() else phone.convert("RGB")

    x0, y0, x1, y1 = PHONE_BOX
    box_w, box_h = x1 - x0, y1 - y0

    # "contain": redimensiona mantendo proporção pra caber dentro da caixa
    scale = min(box_w / phone.width, box_h / phone.height)
    new_w, new_h = max(1, int(phone.width * scale)), max(1, int(phone.height * scale))
    phone_resized = phone.resize((new_w, new_h), Image.LANCZOS)

    paste_x = x0 + (box_w - new_w) // 2
    paste_y = y0 + (box_h - new_h) // 2

    if phone_resized.mode == "RGBA":
        template.paste(phone_resized, (paste_x, paste_y), phone_resized)
    else:
        template.paste(phone_resized, (paste_x, paste_y))

    return template


# ---------------------------------------------------------------------------
# Busca automática (best-effort) no tudocelular.com
# ---------------------------------------------------------------------------


def buscar_url_produto(modelo: str) -> str | None:
    """Usa a busca HTML do DuckDuckGo (site:tudocelular.com) para achar a
    página de ficha técnica do modelo pesquisado. Retorna a URL ou None."""
    query = f"site:tudocelular.com fichas-tecnicas {modelo}"
    url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=10)
    resp.raise_for_status()

    # 1) tenta achar um link direto (sem embrulho)
    matches = TUDOCELULAR_LINK_RE.findall(resp.text)
    if matches:
        return matches[0]

    # 2) tenta achar dentro do parâmetro uddg= do link de redirecionamento
    #    do DuckDuckGo, decodificando a URL antes de procurar o padrão
    for wrapped in DUCKDUCKGO_UDDG_RE.findall(resp.text):
        decoded = unquote(wrapped)
        found = TUDOCELULAR_LINK_RE.findall(decoded)
        if found:
            return found[0]

    return None


def baixar_imagem_produto(url_produto: str) -> bytes | None:
    """Abre a página do produto e extrai a imagem principal (og:image)."""
    resp = requests.get(url_produto, headers=TUDOCELULAR_HEADERS, timeout=10)
    resp.raise_for_status()
    match = OG_IMAGE_RE.search(resp.text)
    if not match:
        return None
    img_url = match.group(1)
    img_resp = requests.get(img_url, headers=TUDOCELULAR_HEADERS, timeout=10)
    img_resp.raise_for_status()
    return img_resp.content


@app.get("/api/buscar")
def api_buscar():
    modelo = (request.args.get("modelo") or "").strip()
    if not modelo:
        return jsonify(ok=False, motivo="Digite o nome do modelo."), 400

    try:
        url_produto = buscar_url_produto(modelo)
        if not url_produto:
            return jsonify(
                ok=False,
                motivo="Não encontrei esse modelo automaticamente. Use a busca manual.",
            )

        imagem_bytes = baixar_imagem_produto(url_produto)
        if not imagem_bytes:
            return jsonify(
                ok=False,
                motivo="Encontrei a página do modelo, mas não consegui extrair a imagem. Use a busca manual.",
                url_produto=url_produto,
            )

        imagem_b64 = base64.b64encode(imagem_bytes).decode("ascii")
        return jsonify(ok=True, imagem_base64=imagem_b64, url_produto=url_produto)

    except requests.exceptions.RequestException as exc:
        return jsonify(
            ok=False,
            motivo=f"Falha ao acessar o site automaticamente ({exc.__class__.__name__}). Use a busca manual.",
        )


@app.post("/api/compor")
def api_compor():
    """Aceita OU um arquivo (multipart, campo 'foto') OU um JSON
    {"imagem_base64": "..."} vindo da busca automática. Retorna o JPG final."""
    phone_bytes = None

    if "foto" in request.files:
        phone_bytes = request.files["foto"].read()
    elif request.is_json:
        data = request.get_json(silent=True) or {}
        b64 = data.get("imagem_base64")
        if b64:
            phone_bytes = base64.b64decode(b64)

    if not phone_bytes:
        return jsonify(ok=False, motivo="Nenhuma imagem de celular recebida."), 400

    try:
        resultado = compose_image(phone_bytes)
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, motivo=f"Não consegui compor a imagem: {exc}"), 400

    buffer = io.BytesIO()
    resultado.save(buffer, format="JPEG", quality=92)
    buffer.seek(0)
    return send_file(buffer, mimetype="image/jpeg", download_name="gshield-hydrogel.jpg")


@app.get("/api/debug3/<tag>")
def api_debug3(tag):
    """Endpoint TEMPORÁRIO: testa se uma URL qualquer é acessível a partir
    daqui e faz uma análise estrutural leve (sem devolver o HTML inteiro),
    pra avaliar rapidamente se um site alternativo bloqueia ou não acesso
    automatizado. Remover depois."""
    import re as _re

    url = request.args.get("url")
    if not url:
        return jsonify(ok=False, motivo="Passe ?url=..."), 400

    try:
        resp = requests.get(url, headers=TUDOCELULAR_HEADERS, timeout=15)
    except requests.exceptions.RequestException as exc:
        return jsonify(ok=False, motivo=f"{exc.__class__.__name__}: {exc}")

    html = resp.text
    html_lower = html.lower()

    title_match = _re.search(r"<title[^>]*>([^<]*)</title>", html, _re.IGNORECASE)
    og_match = OG_IMAGE_RE.search(html)
    img_srcs = _re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html, _re.IGNORECASE)
    img_srcs_relevantes = [
        s for s in img_srcs
        if not any(x in s.lower() for x in ["logo", "amazon", "icon", ".gif", "static/stores"])
    ]
    marcador_specs_photo = "specs-photo" in html_lower
    pos = html_lower.find("specs-photo")
    contexto_specs_photo = html[pos : pos + 400] if pos != -1 else None

    return jsonify(
        ok=True,
        url_testada=url,
        status_code=resp.status_code,
        tamanho_html=len(html),
        titulo_pagina=title_match.group(1).strip() if title_match else None,
        indicios_de_bloqueio=[
            palavra
            for palavra in ["captcha", "cloudflare", "access denied", "permission denied", "blocked", "attention required"]
            if palavra in html_lower
        ],
        og_image=og_match.group(1) if og_match else None,
        marcador_specs_photo_presente=marcador_specs_photo,
        contexto_specs_photo=contexto_specs_photo,
        total_img_tags=len(img_srcs),
        primeiros_20_img_src=img_srcs[:20],
        img_srcs_relevantes_filtrados=img_srcs_relevantes[:15],
    )


@app.get("/api/debug")
def api_debug():
    """Endpoint TEMPORÁRIO de diagnóstico: busca uma URL qualquer e devolve
    o status e o começo do HTML, pra investigar bloqueios/formato de resposta
    sem depender do acesso (bloqueado) do desenvolvedor. Remover depois."""
    url = request.args.get("url")
    if not url:
        return jsonify(ok=False, motivo="Passe ?url=..."), 400
    offset = int(request.args.get("offset", 0))
    limit = int(request.args.get("limit", 15000))
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        return jsonify(
            ok=True,
            status_code=resp.status_code,
            content_type=resp.headers.get("Content-Type"),
            tamanho_total=len(resp.text),
            trecho=resp.text[offset : offset + limit],
        )
    except requests.exceptions.RequestException as exc:
        return jsonify(ok=False, motivo=f"{exc.__class__.__name__}: {exc}")


@app.get("/api/debug2/<tag>")
def api_debug2(tag):
    """Endpoint TEMPORÁRIO: roda a lógica de busca real e devolve só
    contagens/diagnósticos (sem despejar HTML inteiro), pra investigar
    por que buscar_url_produto() não está achando link. Remover depois."""
    import time
    import uuid

    modelo_recebido = request.args.get("modelo")
    modelo = (modelo_recebido or "iPhone 16 Pro").strip()
    query = f"site:tudocelular.com fichas-tecnicas {modelo}"
    url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
    except requests.exceptions.RequestException as exc:
        return jsonify(ok=False, motivo=f"{exc.__class__.__name__}: {exc}")

    html = resp.text
    diretos = TUDOCELULAR_LINK_RE.findall(html)
    uddgs = DUCKDUCKGO_UDDG_RE.findall(html)
    decodificados = [unquote(u) for u in uddgs[:10]]
    achados_apos_decode = [d for d in decodificados if TUDOCELULAR_LINK_RE.findall(d)]

    url_produto = buscar_url_produto(modelo)
    imagem_ok = False
    imagem_tamanho_bytes = None
    og_image_url = None
    if url_produto:
        try:
            resp_produto = requests.get(url_produto, headers=TUDOCELULAR_HEADERS, timeout=15)
            resp_produto.raise_for_status()
            match_og = OG_IMAGE_RE.search(resp_produto.text)
            if match_og:
                og_image_url = match_og.group(1)
                img_resp = requests.get(og_image_url, headers=TUDOCELULAR_HEADERS, timeout=15)
                img_resp.raise_for_status()
                imagem_ok = True
                imagem_tamanho_bytes = len(img_resp.content)
        except requests.exceptions.RequestException as exc:
            og_image_url = f"ERRO: {exc.__class__.__name__}: {exc}"

    body = jsonify(
        ok=True,
        execucao_id=str(uuid.uuid4()),  # prova de que essa execução não é cache
        timestamp=time.time(),
        query_string_bruta=request.query_string.decode(),
        modelo_recebido_no_request=modelo_recebido,
        modelo_usado=modelo,
        url_duckduckgo_montada=url,
        status_code=resp.status_code,
        tamanho_html=len(html),
        contem_palavra_tudocelular=("tudocelular" in html.lower()),
        contem_palavra_resultado_zero=("no results" in html.lower() or "sem resultados" in html.lower()),
        qtd_links_diretos=len(diretos),
        qtd_uddg=len(uddgs),
        og_image_url=og_image_url,
        imagem_baixada_com_sucesso=imagem_ok,
        imagem_tamanho_bytes=imagem_tamanho_bytes,
        primeiros_3_uddg_decodificados=decodificados[:3],
        qtd_achados_apos_decode=len(achados_apos_decode),
        resultado_final=buscar_url_produto(modelo),
    )
    body.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return body


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
