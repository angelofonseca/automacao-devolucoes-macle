"""Texto de um PDF simples (a prévia do DANFE do MACLE) e a conferência dela contra os casos."""
import re
import zlib

from .html import br, so_digitos


def texto_pdf(pdf: bytes) -> str:
    """Texto dos streams FlateDecode com operadores Tj/TJ; T*, Td, TD, Tm e ET quebram a linha."""
    binario = pdf.decode('latin-1')
    linhas = []
    for m in re.finditer(r'stream\r?\n([\s\S]*?)\r?\nendstream', binario):
        try:
            s = zlib.decompress(m.group(1).encode('latin-1')).decode('latin-1')
        except zlib.error:
            continue
        atual = ''
        for op in re.finditer(r'\(((?:\\.|[^\\)])*)\)\s*Tj|\[((?:\\.|[^\]])*)\]\s*TJ|(?<![\w*])(T\*|Td|TD|Tm|ET)(?![\w*])', s):
            if op.group(3):
                if atual.strip():
                    linhas.append(atual)
                atual = ''
                continue
            partes = [op.group(1)] if op.group(1) is not None else re.findall(r'\(((?:\\.|[^\\)])*)\)', op.group(2) or '')
            bruto = ''.join(partes)
            bruto = re.sub(r'\\(\d{3})', lambda x: chr(int(x.group(1), 8)), bruto)
            atual += re.sub(r'\\([nrtbf()\\])', lambda x: '\n' if x.group(1) == 'n' else x.group(1) if x.group(1) in '()\\' else '', bruto)
        if atual.strip():
            linhas.append(atual)
    return '\n'.join(linhas)


def itens_danfe(txt):
    """Linhas de produto: código 'item.tamanho', e a quantidade logo depois da unidade (PAR, UN...)."""
    ls = [s.strip() for s in txt.split('\n')]
    itens = []
    for i, s in enumerate(ls):
        m = re.fullmatch(r'(\d+\.\d+)\.(\w+)', s)
        if not m:
            continue
        un = next((k for k, x in enumerate(ls[i + 1:i + 8]) if re.fullmatch(r'PAR|UN|PC|CX', x, re.I)), -1)
        if un >= 0 and i + 2 + un < len(ls):
            itens.append({'item': m.group(1), 'tamanho': m.group(2), 'qtd': float(ls[i + 2 + un].replace('.', '').replace(',', '.'))})
    return itens


def _num(m):
    return float(m.group(1).replace('.', '').replace(',', '.')) if m else None


def valor_da_nota(txt):
    return _num(re.search(r'VALOR DA NOTA\n([\d.,]+)', txt))


def conferir_danfe(txt, cnpj_loja, cnpj_fornecedor, itens, total, transportadora_cnpj=None, pesos=None, cfops=('5202', '6202')):
    """O que o gerente olharia antes de emitir: emitente, destinatário, CFOP, itens, valor, transporte e pesos.

    itens: lista 'item.tamanho', um por par. Devolve a lista de divergências (vazia = confere).
    """
    div = []
    fmt = lambda c: re.sub(r'^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$', r'\1.\2.\3/\4-\5', so_digitos(c))
    if fmt(cnpj_loja) not in txt:
        div.append(f'emitente: CNPJ da loja {fmt(cnpj_loja)} não aparece')
    if fmt(cnpj_fornecedor) not in txt:
        div.append(f'destinatário: CNPJ {fmt(cnpj_fornecedor)} não aparece')
    if not any(re.search(rf'(^|\n){c}(\n|$)', txt) for c in cfops):
        div.append(f"CFOP {' ou '.join(cfops)} não aparece")
    nota = itens_danfe(txt)
    esperado = {}
    for e in itens:
        item, tam = e.rsplit('.', 1)
        esperado.setdefault(item, []).append(tam)
    for item, tams in esperado.items():
        linhas = [n for n in nota if n['item'] == item]
        if not linhas:
            div.append(f'item {item} não está na nota')
            continue
        if sum(n['qtd'] for n in linhas) != len(tams):
            div.append(f"item {item}: {sum(n['qtd'] for n in linhas):g} par(es) na nota, {len(tams)} nos casos")
    div += [f"item {n['item']}.{n['tamanho']} na nota não é de nenhum caso" for n in nota if n['item'] not in esperado]
    # Desconto da NF de compra é aceito: basta o valor da nota OU o dos produtos bater com a soma dos casos.
    valores = [_num(re.search(rotulo + r'\n([\d.,]+)', txt)) for rotulo in ('VALOR DA NOTA', 'VALOR TOTAL DOS PRODUTOS')]
    if not any(v is not None and abs(v - total) < 0.005 for v in valores):
        div.append(f'valor da nota {valores[0]} ≠ soma dos casos {br(total)}')
    if transportadora_cnpj and fmt(transportadora_cnpj) not in txt:
        div.append(f'transportadora {fmt(transportadora_cnpj)} não aparece')
    if pesos and (pesos['liquido'] not in txt or pesos['bruto'] not in txt):
        div.append(f"pesos {pesos['liquido']}/{pesos['bruto']} não aparecem")
    return div
