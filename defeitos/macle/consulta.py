"""Consulta de um produto com defeito no MACLE (só leitura).

Cada loja só enxerga o que é dela: o primeiro número de todo código é a empresa (loja) no MACLE,
e código de outra loja é recusado.

Caminho, para o código do item com tamanho (ex.: 1.1001.37):
  1. filtro de defeitos pelo item (só a loja, últimos 6 meses) -> defeito em aberto mais recente do tamanho
  2. tela do defeito -> quantidade, situação, observação
  3. cadastro do item -> marca, referência, cor
  4. histórico de compras do item -> NF de compra da loja (a mais recente que tenha o tamanho)
  5. documento de compra -> CNPJ do fornecedor, emissão (marco do prazo) e custo unitário
Para o código do defeito (ex.: 1.60006767) pula o passo 1.
"""
import re
from datetime import date

from .telas import (TelaDesconhecida, campos_filtro, em_aberto, ler_defeito, ler_doc_compra, ler_historico_compra,
                    ler_item, ler_lista_defeitos)

MAX_NFS_TAMANHO = 6  # quantas NFs mais recentes da loja abrir procurando o tamanho


class ProdutoNaoEncontrado(Exception):
    """O MACLE respondeu, mas não há o que devolver (código inválido, sem defeito em aberto, sem NF)."""


def interpretar_codigo(codigo):
    c = re.sub(r'\s+', '', codigo or '')
    m = re.fullmatch(r'(\d{1,3})\.(\d{1,10})', c)
    if m:
        return {'origem': 'defeito', 'empresa': m.group(1), 'numero': m.group(2)}
    m = re.fullmatch(r'(\d{1,3})\.(\d{1,10})\.([A-Za-z0-9]{1,6})', c)
    if m:
        return {'origem': 'item', 'empresa': m.group(1), 'item': f'{m.group(1)}.{m.group(2)}', 'tamanho': m.group(3).upper()}
    raise ProdutoNaoEncontrado('código inválido: use o do defeito (ex.: 1.60006767) ou o do item com tamanho (ex.: 1.93889.36)')


def _ordem_data(d):
    return ''.join(reversed((d or '').split('/')))


def buscar_defeito(sessao, loja, item, tamanho, hoje, ignorar=()):
    """Defeito em aberto mais recente do item+tamanho na loja, fora os já registrados (ignorar)."""
    sessao.get('Defeito?action=cons')  # inicializa o filtro da sessão, como o menu faz
    sessao.filtrar_defeitos(campos_filtro(sessao.get('Defeito?action=cadFiltro'), item, hoje, loja))
    do_tamanho = [d for d in ler_lista_defeitos(sessao.get('Defeito?action=cons'))
                  if d['loja'] == loja and (d['tamanho'] or '').upper() == tamanho]
    livres = sorted((d for d in do_tamanho if em_aberto(d) and d['codigo'] not in ignorar),
                    key=lambda d: _ordem_data(d['cadastro']), reverse=True)
    if not livres:
        raise ProdutoNaoEncontrado(f'nenhum defeito em aberto do item {item} tamanho {tamanho} nos últimos 6 meses'
                                   + (f" (já usados: {', '.join(d['codigo'] for d in do_tamanho)})" if do_tamanho else ''))
    return livres[0]['codigo'], len(livres)


def consultar(sessao, codigo, loja, hoje=None, ignorar=()):
    """Dados do produto para o caso.json. loja: a empresa desta instalação no MACLE (ex.: '1')."""
    hoje = hoje or date.today()
    loja = str(loja)
    c = interpretar_codigo(codigo)
    if c['empresa'] != loja:
        raise ProdutoNaoEncontrado(f"o código {codigo.strip()} é da loja {c['empresa']}; esta automação é da loja {loja}")
    avisos = []
    if c['origem'] == 'item':
        cod_defeito, abertos = buscar_defeito(sessao, loja, c['item'], c['tamanho'], hoje, ignorar)
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

    # Só as NFs da própria loja, da mais recente para a mais antiga.
    da_loja = sorted((x for x in docs if x['loja'] == loja), key=lambda x: _ordem_data(x['movimento']), reverse=True)
    if not da_loja:
        raise ProdutoNaoEncontrado(f'item {item} sem NF de compra na loja {loja}')
    escolhida = reserva = None
    for nf in da_loja[:MAX_NFS_TAMANHO]:
        lido = ler_doc_compra(sessao.get(
            f"modCompras/docCompra/consDataDocCompra.jsp?codEmpresa={nf['loja']}&origem={nf['codForn']}&serie={nf['serie']}&numero={nf['numero']}"),
            item, tamanho)
        if lido['itemNaNota'] and lido['tamanhoNaNota'] is not False:
            escolhida = (nf, lido)
            break
        reserva = reserva or (nf, lido)
    if not escolhida:
        escolhida = reserva
        avisos.append(f'tamanho {tamanho} não consta nas NFs recentes; usada a NF {reserva[0]["numero"]}')
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
