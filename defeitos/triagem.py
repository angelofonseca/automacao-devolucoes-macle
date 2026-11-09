"""Triagem por regras fixas: o que trava, o que espera fechar o lote e quais rascunhos montar.

Entrada: casos já enriquecidos com os dados do ERP + cadastro de fornecedores (JSON).
Saída: um plano em JSON (dict) — nada é gravado aqui; o pipeline aplica o plano.

Regras:
1. Fornecedor achado pelo CNPJ da NF (8 primeiros dígitos). Fora do cadastro -> trava.
2. Fornecedor sem e-mail de contato -> trava (não há para quem escrever).
3. Fora do prazo -> NÃO trava: o e-mail sai com "fora do prazo" no assunto e a fábrica decide.
4. Casos da mesma loja + mesmo fornecedor formam um lote. O rascunho só sai quando o lote
   atinge o mínimo em R$ (minimoLote) e em pares (minimoPares) do fornecedor.
"""
import re

from . import prazo as regra_prazo


def so_digitos(texto):
    return re.sub(r'\D', '', texto or '')


def fornecedor_por_cnpj(cnpj, fornecedores):
    raiz = so_digitos(cnpj)[:8]
    return next((f for f in fornecedores if raiz and raiz in f.get('cnpjRaiz', [])), None)


def avaliar(caso, fornecedores, hoje):
    """{'fornecedor', 'trava': [...], 'avisos': [...], 'prazo'} de um caso com dados do ERP."""
    nf = caso['erp']['nf']
    f = fornecedor_por_cnpj(nf['cnpjFornecedor'], fornecedores)
    if not f:
        return {'fornecedor': None, 'trava': [f"CNPJ {nf['cnpjFornecedor']} fora do cadastro de fornecedores"],
                'avisos': [], 'prazo': None}
    trava, avisos = [], []
    if not (f.get('contato') or {}).get('email'):
        trava.append(f"{f['nome']}: sem e-mail de contato cadastrado")
    p = regra_prazo.avaliar(f.get('prazo'), nf['emissao'], hoje)
    if p['situacao'] == 'fora':
        avisos.append(f"fora do prazo ({p['motivo']})")
    elif p['situacao'] == 'indefinido':
        avisos.append(f"prazo indefinido ({p['motivo']})")
    return {'fornecedor': f, 'trava': trava, 'avisos': avisos, 'prazo': p}


def corpo_rascunho(itens, loja, agora):
    """Texto do e-mail: saudação, loja, um bloco por item, fotos em anexo."""
    pares = sum(i['quantidade'] for i in itens)
    linhas = [('Bom dia' if agora.hour < 12 else 'Boa tarde') + ', tudo bem?',
              'Segue um par com defeito:' if pares == 1 else f'Seguem {pares} pares com defeito:', '']
    if loja:
        linhas += [loja['razao'], f"CNPJ {loja['cnpj']}", '']
    for i in itens:
        linhas += [f"REF: {i['referencia']}", f"NUMERAÇÃO: {i['tamanho']}",
                   f"QTD: {i['quantidade']} {'par' if i['quantidade'] == 1 else 'pares'}",
                   f"NF ORIGEM: {i['nf']}", f"DEFEITO: {i['defeito']}", '']
    linhas += ['Fotos em anexo.', '', 'Atenciosamente,']
    return '\n'.join(linhas)


def planejar(casos, cadastro, lojas, hoje, agora):
    """Agrupa os casos em lotes e decide o destino de cada um.

    casos: lista de dicts (caso.json) já com o campo 'erp'.
    cadastro: conteúdo de fornecedores.json ({'fornecedores': [...]}); lojas: {número: {'razao', 'cnpj'}} do lojas.json.
    O lote é da loja da NF de compra (é ela que emite a NFD), não da loja que recebeu o par.
    """
    fornecedores = cadastro['fornecedores']
    bloquear, aguardar, rascunhos = [], [], []
    lotes = {}

    for c in casos:
        av = avaliar(c, fornecedores, hoje)
        if av['trava']:
            bloquear.append({'protocolo': c['protocolo'], 'motivo': '; '.join(av['trava'])})
            continue
        f = av['fornecedor']
        loja = c['erp']['nf'].get('loja') or c['loja']
        chave = f"L{loja}-{f['id']}"   # lote = mesma loja + mesmo fornecedor
        lote = lotes.setdefault(chave, {'chave': chave, 'loja': loja, 'fornecedor': f, 'casos': [], 'avisos': [], 'fora': []})
        lote['casos'].append(c)
        lote['avisos'] += [f"#{c['protocolo']}: {a}" for a in av['avisos']]
        if av['prazo'] and av['prazo']['situacao'] == 'fora':
            lote['fora'].append(c['protocolo'])

    for lote in lotes.values():
        f = lote['fornecedor']
        total = round(sum(c['erp']['custo'] * c['quantidade'] for c in lote['casos']), 2)
        pares = sum(c['quantidade'] for c in lote['casos'])
        minimo, minimo_pares = f.get('minimoLote') or 0, f.get('minimoPares') or 0
        protocolos = [c['protocolo'] for c in lote['casos']]
        if total < minimo or pares < minimo_pares:
            aguardar.append({'chave': lote['chave'], 'fornecedor': f['nome'], 'loja': lote['loja'], 'protocolos': protocolos,
                             'total': total, 'minimo': minimo, 'falta': round(max(0, minimo - total), 2),
                             'pares': pares, 'minimoPares': minimo_pares})
            continue
        itens = [{'protocolo': c['protocolo'], 'referencia': c['erp']['referencia'], 'tamanho': c['erp']['tamanho'],
                  'quantidade': c['quantidade'], 'nf': c['erp']['nf']['numero'], 'defeito': c['defeito'],
                  'fotos': c.get('fotos', [])} for c in lote['casos']]
        rascunhos.append({
            'chave': lote['chave'], 'fornecedor': f['nome'], 'loja': lote['loja'], 'para': [f['contato']['email']],
            'assunto': ('Produto com defeito' if pares == 1 else 'Produtos com defeito') + (' - fora do prazo' if lote['fora'] else ''),
            'corpo': corpo_rascunho(itens, lojas.get(lote['loja']), agora),
            'protocolos': protocolos, 'total': total, 'pares': pares, 'itens': itens, 'avisos': lote['avisos'],
        })

    return {'bloquear': bloquear, 'aguardarLote': aguardar, 'montarRascunho': rascunhos}
