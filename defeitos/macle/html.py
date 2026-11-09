"""Leitura das páginas HTML do MACLE: texto visível, campos de formulário e números no formato brasileiro."""
import html as _html
import re


def decodificar(dados: bytes) -> str:
    """O MACLE mistura UTF-8 e latin-1 na mesma página: sequência UTF-8 válida vira UTF-8, byte solto vira latin-1."""
    saida, i = [], 0
    while i < len(dados):
        c = dados[i]
        n = 3 if 0xF0 <= c <= 0xF4 else 2 if 0xE0 <= c <= 0xEF else 1 if 0xC2 <= c <= 0xDF else 0
        seq = dados[i + 1:i + 1 + n]
        if c >= 0x80 and n and len(seq) == n and all(x & 0xC0 == 0x80 for x in seq):
            saida.append(dados[i:i + 1 + n].decode('utf-8'))
            i += 1 + n
        else:
            saida.append(chr(c))
            i += 1
    return ''.join(saida)


def texto(pagina: str) -> str:
    """Texto visível: células separadas por tab, linhas por \\n (o que a pessoa leria na tela)."""
    t = re.sub(r'<(script|style|head)\b[\s\S]*?</\1>', ' ', pagina, flags=re.I)
    t = re.sub(r'<br\s*/?>|</(tr|p|div|li|h\d|table|label)>', '\n', t, flags=re.I)
    t = re.sub(r'</t[dh]>', '\t', t, flags=re.I)
    t = _html.unescape(re.sub(r'<[^>]+>', ' ', t))
    linhas = []
    for linha in t.split('\n'):
        linha = re.sub(r' *\t *', '\t', re.sub(r'[  ]+', ' ', linha)).strip()
        if linha.replace('\t', '').strip():
            linhas.append(linha)
    return '\n'.join(linhas)


def limpo(s):
    s = re.sub(r'\s+', ' ', s or '').strip()
    return s or None


def _valor(tag):
    m = re.search(r'\bvalue=(?:"([^"]*)"|\'([^\']*)\'|([^\s>]*))', tag, re.I)
    return None if not m else next((g for g in m.groups() if g is not None), '')


def campo(pagina: str, nome: str):
    """Valor de <input name=nome>, texto de <textarea name=nome> ou a opção selecionada de <select name=nome>."""
    m = re.search(rf'<textarea[^>]*\bname=["\']?{nome}\b[^>]*>([\s\S]*?)</textarea>', pagina, re.I)
    if m:
        return limpo(_html.unescape(m.group(1)))
    m = re.search(rf'<select[^>]*\bname=["\']?{nome}\b[^>]*>([\s\S]*?)</select>', pagina, re.I)
    if m:
        opt = re.search(r'<option\b[^>]*\bselected\b[^>]*>([^<]*)', m.group(1), re.I)
        return limpo(_html.unescape(opt.group(1))) if opt else None
    m = re.search(rf'<input\b[^>]*\bname=["\']?{nome}["\'\s>][^>]*>', pagina, re.I)
    return limpo(_html.unescape(_valor(m.group(0)) or '')) if m else None


def textarea_bruto(pagina: str, nome: str):
    """Conteúdo de <textarea> sem mexer nas quebras de linha (a observação fiscal vai como a tela gerou)."""
    m = re.search(rf'<textarea[^>]*\bname=["\']?{nome}\b[^>]*>([\s\S]*?)</textarea>', pagina, re.I)
    return _html.unescape(m.group(1)) if m else None


def campos_form(pagina: str, nome=None) -> dict:
    """Campos de um formulário como o navegador enviaria: inputs com valor, checkbox marcado, opção selecionada."""
    forms = re.findall(r'<form\b[\s\S]*?</form>', pagina, re.I)
    form = next((f for f in forms if re.search(rf'\bname=["\']?{nome}\b', f, re.I)), None) if nome else (forms[0] if forms else None)
    if not form:
        raise ValueError(f'formulário {nome or ""} não encontrado na tela')
    campos = {}
    for tag in re.findall(r'<input\b[^>]*>', form, re.I):
        n = re.search(r'\bname=["\']?(\w+)', tag, re.I)
        tipo = (re.search(r'\btype=["\']?(\w+)', tag, re.I) or [None, 'text'])[1].lower()
        if not n or tipo in ('button', 'submit', 'image', 'file'):
            continue
        if tipo in ('checkbox', 'radio') and not re.search(r'\bchecked\b', tag, re.I):
            continue
        v = _valor(tag)
        campos[n.group(1)] = v if v is not None else ('on' if tipo == 'checkbox' else '')
    for n, opcoes in re.findall(r'<select\b[^>]*\bname=["\']?(\w+)[^>]*>([\s\S]*?)</select>', form, re.I):
        opt = re.search(r'<option\b[^>]*\bselected\b[^>]*>', opcoes, re.I) or re.search(r'<option\b[^>]*>', opcoes, re.I)
        campos[n] = (_valor(opt.group(0)) or '') if opt else ''
    for n, conteudo in re.findall(r'<textarea\b[^>]*\bname=["\']?(\w+)[^>]*>([\s\S]*?)</textarea>', form, re.I):
        campos[n] = _html.unescape(conteudo)
    return campos


def numero(s):
    """'1.234,56' -> 1234.56; '--' ou vazio -> None."""
    v = (s or '').strip()
    if not re.fullmatch(r'-?[\d.]+(,\d+)?', v):
        return None
    return float(v.replace('.', '').replace(',', '.'))


def br(valor: float, casas=2) -> str:
    """1234.5 -> '1234,50' (como o MACLE escreve no DANFE e nos formulários)."""
    return f'{valor:.{casas}f}'.replace('.', ',')


def so_digitos(s) -> str:
    return re.sub(r'\D', '', s or '')


def normal(s) -> str:
    """Comparação sem acento, espaço nem pontuação: 'São João' == 'SAO JOAO'."""
    import unicodedata
    return re.sub(r'[^\w]', '', unicodedata.normalize('NFD', s or '').encode('ascii', 'ignore').decode()).upper()
