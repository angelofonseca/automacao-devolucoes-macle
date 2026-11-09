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


def campos_filtro(pagina, item, hoje):
    """Campos do filtro de defeitos como o botão OK enviaria, com: todas as empresas, o item e os últimos 6 meses."""
    from .html import campos_form
    todas = re.search(r"id=[\"']?empTodas[^>]*setEmpresas\('([\d,]+)'\)", pagina, re.I)
    try:
        c = campos_form(pagina)
    except ValueError:
        c = None
    if c is None or not todas:
        raise TelaDesconhecida('tela de filtro de defeitos não reconhecida')
    ini, fim = periodo_cadastro(hoje)
    c.update(action='setFilterDefeito', empresas=todas.group(1), codItem=item, dataCadIni=ini, dataCadFim=fim)
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


# ---------------------------------------------------------------- NFD (devolução ao fornecedor)

def contar_pendentes(pagina):
    """'Existe(m) N documento(s) para continuar a devolução' na lista de devoluções."""
    m = re.search(r'Existe\(m\)\s+(\d+)\s+documento', texto(pagina), re.I)
    return int(m.group(1)) if m else 0


def ler_pendentes(pagina):
    """Rascunhos pendentes (continuaDevolucao.jsp): Tipo | Fornecedor | Produtos | Qtd. Total."""
    saida = []
    for tr in re.findall(r'<tr\b[\s\S]*?</tr>', pagina, re.I):
        c = [re.sub(r'\s+', ' ', texto(td)).strip() for td in re.findall(r'<td\b[^>]*>([\s\S]*?)</td>', tr, re.I)]
        if len(c) >= 4 and re.search(r'devol', c[0], re.I):
            saida.append({'tipo': c[0], 'fornecedor': c[1], 'produtos': int(c[2] or 0), 'qtd': c[3]})
    return saida


def destino(pagina):
    """Para onde a resposta manda a página principal (location.href / gotoFechamto) dentro da devolução."""
    if re.search(r'\bgotoFechamto\s*\(', pagina) and not re.search(r'function\s+gotoFechamto', pagina):
        return 'DevolFornecedor?action=fechamto&offset=0'
    if re.search(r'function\s+\w+\s*\(', pagina):  # tela completa: os endereços estão dentro dos botões
        return None
    m = re.search(r"location(?:\.href)?\s*=\s*['\"]((?:DevolFornecedor|modEstoque/devolFornecedor/)[^'\"]+)['\"]", pagina)
    return m.group(1) if m else None


def no_fechamento(pagina):
    """Página principal pronta para finalizar: formulário com action=fechamto."""
    return bool(re.search(r'name=["\']?action["\']?\s+value=["\']?fechamto', pagina, re.I))


def ler_item_origem(pagina):
    """Tela 'Selecione o item de origem': item sendo devolvido e as linhas do XML da NF de compra (associar(n))."""
    t = texto(pagina)
    dev = re.search(r'Devolvendo agora:?[\s\S]*?\n?(\d+\.\d+\.\w+)\s*-\s*([^\n]+)', t, re.I)
    linhas = []
    for tr in re.split(r'<tr\b', pagina, flags=re.I)[1:]:
        n = re.search(r'associar\(\s*(\d+)\s*\)', tr)
        if not n:
            continue
        tds = [texto(td).strip() for td in re.split(r'</td>', tr, flags=re.I)] + [''] * 4
        linhas.append({'nitem': n.group(1), 'codigo': tds[1], 'descricao': tds[2], 'qtd': tds[3]})
    assoc = re.search(r"function associar\(nitem\)\s*\{\s*window\.location\.href\s*=\s*'([^']*)'\s*\+\s*nitem\s*\+\s*'([^']*)'", pagina)
    conf = re.search(r"function confirmar\(\)\s*\{\s*window\.location\.href\s*=\s*'([^']*)'", pagina)
    return {'codigo': dev.group(1) if dev else None, 'descricao': dev.group(2).strip() if dev else None, 'linhas': linhas,
            'associar': assoc.groups() if assoc else None, 'confirmar': conf.group(1) if conf else None}


def escolher_linha(linhas, referencia, tamanho, cor=None):
    """Linha da NF que é o item do caso: referência no código ou na descrição, depois o tamanho, depois a cor.

    Devolve (linha, etapas). Nenhuma ou mais de uma linha no fim -> linha None: o bot para e não chuta.
    """
    tam = re.escape(tamanho.upper())
    ref = normal(referencia)
    etapas = []
    cod = lambda l: (l.get('codigo') or '').split('/')[0].strip()
    tem_tam = lambda l: bool(re.search(rf'(^|\s){tam}$', l['descricao'].strip(), re.I) or re.search(rf'(^|[-_\s]){tam}$', cod(l), re.I)
                             or re.search(rf'\btam\.?\s*{tam}\b', l['descricao'], re.I))
    ok = [l for l in linhas if not ref or ref in normal(l['descricao']) or ref in normal(cod(l))]
    etapas.append(f'ref. {referencia}: {len(ok)} linha(s)')
    ok = [l for l in ok if tem_tam(l)]
    etapas.append(f'tam. {tamanho}: {len(ok)} linha(s)')
    for parte in [p.strip() for p in (cor or '').split('/') if p.strip()]:
        if len(ok) > 1:
            com_cor = [l for l in ok if normal(parte) in normal(l['descricao'])]
            etapas.append(f'cor {parte}: {len(com_cor)} linha(s)')
            ok = com_cor or ok
    return (ok[0] if len(ok) == 1 else None), etapas


def ler_transportadoras(pagina):
    """Opções do select de transportadora: código interno e 'NOME - CNPJ'."""
    sel = re.search(r'<select\b[^>]*\bname=["\']?selectTransp\b[^>]*>([\s\S]*?)</select>', pagina, re.I)
    opcoes = []
    for codigo, txt in re.findall(r'<option\b[^>]*\bvalue=["\']?([^"\'\s>]*)[^>]*>([^<]*)', sel.group(1) if sel else '', re.I):
        if codigo and codigo not in ('0', '-1'):
            cnpj = re.search(r'[\d./-]{14,18}', txt)
            opcoes.append({'codigo': codigo, 'texto': texto(txt).strip(), 'cnpj': so_digitos(cnpj.group(0) if cnpj else '')})
    return opcoes
