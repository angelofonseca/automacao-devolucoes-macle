"""Uma função por tela do MACLE: recebe o HTML, devolve um dict.

Portado do cliente que roda em produção. Os comentários dizem que parte da tela cada regex procura.
"""
import re
from datetime import date

from .html import campo, limpo, normal, numero, so_digitos, texto

DATA = r'(\d{2}/\d{2}/\d{4})'


class TelaDesconhecida(Exception):
    """A página não tem o formato esperado (tela mudou, sessão caiu ou registro inexistente)."""


def _depois(t, rotulo, valor=r'([^\n\t]+)'):
    """Valor que vem logo depois de um rótulo no texto da tela ('Tamanho  37' -> '37')."""
    m = re.search(rf'{rotulo}[\s:]*{valor}', t, re.I)
    return limpo(m.group(1)) if m else None


def _celulas(tr):
    return [limpo(texto(td)) or '' for td in re.split(r'</td>', tr, flags=re.I)]


# ---------------------------------------------------------------- consulta de produto

def ler_defeito(pagina):
    """Tela do defeito (Defeito?action=edit): item, tamanho, quantidade, situação, local, cadastro e observação."""
    t = texto(pagina)
    item = re.search(r'Item\s+(\d+\.\d+)\s*([^\n\t]*)', t, re.I)
    if not item:
        raise TelaDesconhecida('defeito não encontrado no ERP')
    return {
        'item': item.group(1),
        'descricao': limpo(item.group(2)),
        'tamanho': _depois(t, 'Tamanho', r'([^\s\t]+)'),
        'quantidade': numero(_depois(t, 'Quantidade', r'([\d.,]+)')),
        'situacao': _depois(t, r'Situa\S*o'),
        'local': _depois(t, 'Local'),
        'cadastro': _depois(t, 'Cadastro', DATA),
        'observacao': campo(pagina, 'obs'),
    }


def ler_item(pagina):
    """Cadastro do item (Item?action=edit): marca, referência do fornecedor, descrição, tipo, cor e grade."""
    marca, ref, desc = campo(pagina, 'codMarca'), campo(pagina, 'refFornecedor'), campo(pagina, 'descricao')
    if not (marca or ref or desc):
        raise TelaDesconhecida('item não encontrado no ERP')

    def nivel(n):  # selects codNivelN; "-- informe --" = vazio
        v = campo(pagina, f'codNivel{n}')
        return v if v and not re.match(r'^-+\s*informe', v, re.I) else None
    return {'marca': marca if marca and not re.match(r'^-+\s*informe', marca, re.I) else None, 'referencia': ref,
            'descricao': desc, 'tipo': nivel(2), 'cor': nivel(6), 'grade': nivel(7), 'fornecedor': nivel(8)}


def ler_historico_compra(pagina):
    """Compras do item: cada linha tem showDataDocCompra(loja, fornecedor, 'série', número) | Data | Qtd | Unit | Valor."""
    docs = []
    for tr in re.split(r'<tr\b', pagina, flags=re.I)[1:]:
        d = re.search(r"showDataDocCompra\(\s*(\d+)\s*,\s*(\d+)\s*,\s*'([^']*)'\s*,\s*(\d+)\s*\)", tr)
        if not d:
            continue
        tds = _celulas(tr)
        mov = re.search(DATA, texto(tr))
        docs.append({'documento': '.'.join(d.groups()), 'loja': d.group(1), 'codForn': d.group(2), 'serie': d.group(3),
                     'numero': d.group(4), 'movimento': mov.group(1) if mov else None,
                     'qtd': numero(tds[2]) if len(tds) > 2 else None, 'custoUnitario': numero(tds[3]) if len(tds) > 3 else None})
    return docs


def ler_doc_compra(pagina, item, tamanho=None):
    """Documento de compra: fornecedor (código - CNPJ - nome), datas, status, transportadora e o bloco do item.

    No bloco do item vêm os tamanhos da grade, o rótulo "Qtd" e, na linha seguinte, as quantidades por tamanho
    na mesma ordem, a Qtd total e o Prc.Unit. Daí sai se o tamanho do caso está na nota e o custo.
    """
    t = texto(pagina)
    forn = re.search(r'Fornecedor\s+\d+\s*-\s*([\d./-]{14,18})\s*-\s*([^\n\t]+)', t, re.I)
    bloco = next((b for b in re.split(r'\n(?=Item\s+\d+\.\d+\s*-)', t, flags=re.I)
                  if re.match(rf'Item\s+{re.escape(item)}\s*-', b, re.I)), None)
    tamanho_na_nota = custo = None
    if bloco:
        cel = [c.strip() for c in re.split(r'[\n\t]', bloco) if c.strip()]
        i_qtd = next((i for i, c in enumerate(cel) if re.fullmatch(r'Qtd', c, re.I)), -1)
        ini = i_qtd
        while ini > 0 and re.fullmatch(r'[A-Z0-9]{1,4}', cel[ini - 1], re.I) and not re.fullmatch(r'PCM|MKP|PV', cel[ini - 1], re.I):
            ini -= 1
        tams = cel[ini:i_qtd]
        i_num = i_qtd + 1
        while i_num < len(cel) and not re.fullmatch(r'\d+(,\d+)?', cel[i_num]):
            i_num += 1
        qtds = cel[i_num:i_num + len(tams)]
        if i_qtd > 0 and tams and len(qtds) == len(tams):
            custo = numero(cel[i_num + len(tams) + 1]) if i_num + len(tams) + 1 < len(cel) else None
            if tamanho:
                tamanho_na_nota = tamanho in tams and float(qtds[tams.index(tamanho)].replace(',', '.')) > 0
    nfe = re.search(r'showDataNFe\((\d+),\s*(\d+)\)', pagina)
    return {'emissao': _depois(t, r'Data emiss\.?', DATA), 'movimento': _depois(t, r'Data movto\.?', DATA),
            'cnpjFornecedor': forn.group(1) if forn else None, 'fornecedor': limpo(forn.group(2)) if forn else None,
            'status': _depois(t, 'Status', r'(\w+)'), 'transportadora': _depois(t, 'Transportadora'),
            'nfe': nfe.groups() if nfe else None, 'itemNaNota': bool(bloco), 'tamanhoNaNota': tamanho_na_nota,
            'custoUnitario': custo}


def periodo_cadastro(hoje: date):
    """Filtro de defeitos: do dia 1º de cinco meses atrás ao último dia do mês atual (6 meses)."""
    m = hoje.month - 5
    ini = date(hoje.year + (m - 1) // 12, (m - 1) % 12 + 1, 1)
    prox = date(hoje.year + hoje.month // 12, hoje.month % 12 + 1, 1)
    fim = date.fromordinal(prox.toordinal() - 1)
    return ini.strftime('%d/%m/%Y'), fim.strftime('%d/%m/%Y')


def campos_filtro(pagina, item, hoje, empresa):
    """Campos do filtro de defeitos como o botão OK enviaria, com: só a empresa (loja), o item e os últimos 6 meses."""
    from .html import campos_form
    todas = re.search(r"id=[\"']?empTodas[^>]*setEmpresas\('([\d,]+)'\)", pagina, re.I)
    try:
        c = campos_form(pagina)
    except ValueError:
        c = None
    if c is None or not todas:
        raise TelaDesconhecida('tela de filtro de defeitos não reconhecida')
    if str(empresa) not in todas.group(1).split(','):
        raise TelaDesconhecida(f'a loja {empresa} não aparece no filtro de defeitos do MACLE')
    ini, fim = periodo_cadastro(hoje)
    c.update(action='setFilterDefeito', empresas=str(empresa), codItem=item, dataCadIni=ini, dataCadFim=fim)
    c.pop('pendCli', None)
    return c


def ler_lista_defeitos(pagina):
    """Lista de defeitos: editDefeito(empresa, número) | descrição | tamanho | qtd | cadastro | local | situação."""
    linhas = []
    for tr in re.split(r'<tr\b', pagina, flags=re.I)[1:]:
        cod = re.search(r'editDefeito\(\s*(\d+)\s*,\s*(\d+)\s*\)', tr)
        if not cod:
            continue
        tds = _celulas(tr) + [''] * 7
        linhas.append({'codigo': f'{cod.group(1)}.{cod.group(2)}', 'loja': cod.group(1), 'descricao': tds[1],
                       'tamanho': tds[2], 'cadastro': tds[4], 'local': tds[5], 'situacao': tds[6]})
    return linhas


def em_aberto(d):
    """Defeito ainda na loja: situação Pendente e fora do local Fornecedor."""
    return bool(re.fullmatch(r'pendente', d.get('situacao') or '', re.I)) and not re.fullmatch(r'fornecedor', d.get('local') or '', re.I)

