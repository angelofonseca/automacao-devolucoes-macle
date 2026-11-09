"""Consulta de um produto com defeito no MACLE (só leitura).

Caminho, para o código do item com tamanho (ex.: 1.1001.37):
  1. filtro de defeitos pelo item (todas as lojas, últimos 6 meses) -> defeito em aberto mais recente do tamanho
  2. tela do defeito -> quantidade, situação, observação
  3. cadastro do item -> marca, referência, cor
  4. histórico de compras do item -> NF de compra pela ordem de lojas (a primeira que tenha o tamanho)
  5. documento de compra -> CNPJ do fornecedor, emissão (marco do prazo) e custo unitário
Para o código do defeito (ex.: 6.60006767) pula o passo 1.
"""
import re
from datetime import date

from .telas import (TelaDesconhecida, campos_filtro, em_aberto, ler_defeito, ler_doc_compra, ler_historico_compra,
                    ler_item, ler_lista_defeitos)

MAX_NFS_TAMANHO = 6  # quantas NFs mais recentes de cada loja abrir procurando o tamanho


class ProdutoNaoEncontrado(Exception):
    """O MACLE respondeu, mas não há o que devolver (código inválido, sem defeito em aberto, sem NF)."""


def interpretar_codigo(codigo):
    c = re.sub(r'\s+', '', codigo or '')
    m = re.fullmatch(r'(\d{1,3})\.(\d{1,10})', c)
    if m:
        return {'origem': 'defeito', 'empresa': m.group(1), 'numero': m.group(2)}
    m = re.fullmatch(r'(\d{1,3})\.(\d{1,10})\.([A-Za-z0-9]{1,6})', c)
    if m:
        return {'origem': 'item', 'item': f'{m.group(1)}.{m.group(2)}', 'tamanho': m.group(3).upper()}
    raise ProdutoNaoEncontrado('código inválido: use o do defeito (ex.: 6.60006767) ou o do item com tamanho (ex.: 1.93889.36)')


def _ordem_data(d):
    return ''.join(reversed((d or '').split('/')))


def buscar_defeito(sessao, item, tamanho, hoje, ignorar=()):
    """Defeito em aberto mais recente do item+tamanho, fora os já registrados (ignorar)."""
    sessao.get('Defeito?action=cons')  # inicializa o filtro da sessão, como o menu faz
    sessao.filtrar_defeitos(campos_filtro(sessao.get('Defeito?action=cadFiltro'), item, hoje))
    do_tamanho = [d for d in ler_lista_defeitos(sessao.get('Defeito?action=cons')) if (d['tamanho'] or '').upper() == tamanho]
    livres = sorted((d for d in do_tamanho if em_aberto(d) and d['codigo'] not in ignorar),
                    key=lambda d: _ordem_data(d['cadastro']), reverse=True)
    if not livres:
        raise ProdutoNaoEncontrado(f'nenhum defeito em aberto do item {item} tamanho {tamanho} nos últimos 6 meses'
                                   + (f" (já usados: {', '.join(d['codigo'] for d in do_tamanho)})" if do_tamanho else ''))
    return livres[0]['codigo'], len(livres)


def consultar(sessao, codigo, ordem_lojas, hoje=None, ignorar=()):
    """Dados do produto para o caso.json. ordem_lojas: lojas na ordem de preferência para escolher a NF."""
    hoje = hoje or date.today()
    c = interpretar_codigo(codigo)
    avisos = []
    if c['origem'] == 'item':
        cod_defeito, abertos = buscar_defeito(sessao, c['item'], c['tamanho'], hoje, ignorar)
        if abertos > 1:
            avisos.append(f"{abertos} defeitos em aberto do item {c['item']} tamanho {c['tamanho']}; usado o mais recente, {cod_defeito}")
    else:
        cod_defeito = f"{c['empresa']}.{c['numero']}"
    emp_def, num_def = cod_defeito.split('.')
    try:
        d = ler_defeito(sessao.get(f'Defeito?action=edit&codEmpresa={emp_def}&codDefeito={num_def}'))
    except TelaDesconhecida as exc:
        raise ProdutoNaoEncontrado(f'defeito {cod_defeito}: {exc}')
    if not em_aberto(d):
        raise ProdutoNaoEncontrado(f"defeito {cod_defeito} não está pendente na loja ({d['situacao']} / {d['local']})")
    item, tamanho = d['item'], (d['tamanho'] or c.get('tamanho') or '').upper()

    emp_item, cod_item = item.split('.')
    it = ler_item(sessao.get(f'Item?action=edit&codEmpItem={emp_item}&codItem={cod_item}'))
    docs = ler_historico_compra(sessao.get(f'modEstoque/item/consHistoricoCompra.jsp?codEmpItem={emp_item}&codItem={cod_item}'))
    if not docs:
        raise ProdutoNaoEncontrado(f'item {item} sem NF de compra no histórico')

    # Lojas: a ordem configurada primeiro, depois as outras que compraram (da que mais comprou para a que menos).
    compras = {}
    for doc in docs:
        compras[doc['loja']] = compras.get(doc['loja'], 0) + (doc['qtd'] or 0)
    ordem = list(dict.fromkeys([l for l in ordem_lojas if l in compras] + sorted(compras, key=lambda l: (-compras[l], int(l)))))
    escolhida = reserva = None
    for loja in ordem:
        da_loja = sorted((x for x in docs if x['loja'] == loja), key=lambda x: _ordem_data(x['movimento']), reverse=True)
        for nf in da_loja[:MAX_NFS_TAMANHO]:
            lido = ler_doc_compra(sessao.get(
                f"modCompras/docCompra/consDataDocCompra.jsp?codEmpresa={nf['loja']}&origem={nf['codForn']}&serie={nf['serie']}&numero={nf['numero']}"),
                item, tamanho)
            if lido['itemNaNota'] and lido['tamanhoNaNota'] is not False:
                escolhida = (nf, lido)
                break
            reserva = reserva or (nf, lido)
        if escolhida:
            break
    if not escolhida:
        escolhida = reserva
        avisos.append(f'tamanho {tamanho} não consta nas NFs recentes; usada a NF {reserva[0]["numero"]} da loja {reserva[0]["loja"]}')
    elif ordem_lojas and escolhida[0]['loja'] != ordem_lojas[0]:
        avisos.append(f"usada NF {escolhida[0]['numero']} da loja {escolhida[0]['loja']} (loja {ordem_lojas[0]} sem o item/tamanho)")
    nf, doc = escolhida
    custo = doc['custoUnitario'] if doc['custoUnitario'] is not None else nf['custoUnitario']
    if custo is None or not doc['cnpjFornecedor'] or not doc['emissao']:
        raise ProdutoNaoEncontrado(f"NF {nf['documento']} sem custo, CNPJ ou data de emissão legível")
    return {
        'codigoDefeito': cod_defeito, 'item': item, 'tamanho': tamanho, 'quantidade': int(d['quantidade'] or 1),
        'marca': it['marca'], 'referencia': it['referencia'], 'descricao': it['descricao'] or d['descricao'], 'cor': it['cor'],
        'custo': round(custo, 2), 'observacao': d['observacao'],
        'nf': {'documento': nf['documento'], 'loja': nf['loja'], 'numero': nf['numero'], 'serie': nf['serie'],
               'emissao': doc['emissao'], 'cnpjFornecedor': doc['cnpjFornecedor'], 'fornecedor': doc['fornecedor']},
        'avisos': avisos,
    }
