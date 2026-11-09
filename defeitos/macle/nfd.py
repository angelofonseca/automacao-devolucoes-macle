"""Rascunho da NFD (nota fiscal de devolução ao fornecedor) no MACLE, gravado em "Continuar depois".

O bot faz os passos 0 a 9 da tela e para. Emitir (enviar ao SEFAZ) é sempre humano: a sessão nem permite.
Qualquer tela não reconhecida para antes de gravar (o documento aberto na sessão se perde, nada fica salvo).

  0  lista de devoluções: já existe rascunho do mesmo fornecedor? -> para
  1  nova devolução + Enter
  2  fornecedor + tipo MERCADORIA -> lista os defeitos pendentes do fornecedor
  4  marca o defeito -> lista as NFs de origem
  5  escolhe a NF de origem e confirma os defeitos dela
  6  associa cada item à linha da NF de compra (quando o ERP não casa sozinho)
  7  dados do transporte (transportadora, espécie, volumes, pesos)
  8  prévia do DANFE (PDF) conferida contra os casos
  9  gravar e sair ("Continuar depois")
"""
import re
import time
from urllib.parse import quote

from ..resiliencia import ErroIntegracao
from .html import br, campo, campos_form, normal, so_digitos, textarea_bruto, texto
from .pdf import conferir_danfe, texto_pdf, valor_da_nota
from .telas import (contar_pendentes, destino, escolher_linha, ler_item_origem, ler_pendentes, ler_transportadoras,
                    no_fechamento)


class Parada(Exception):
    """Motivo de negócio para não gravar o rascunho (vai para decisão humana)."""


def pesos(pares, por_par=0.7, caixa=0.2):
    """Tudo numa caixa: peso por par + a caixa (os valores vêm do .env)."""
    return {'liquido': br(por_par * pares, 3), 'bruto': br(por_par * pares + caixa, 3)}


def partes_documento(doc):
    """'1.45.7.4501' -> loja 1, fornecedor interno 45, série 7, número 4501."""
    m = re.fullmatch(r'(\d+)\.(\d+)\.(\w+)\.(\d+)', doc or '')
    if not m:
        raise Parada(f'documento de compra inválido: {doc!r}')
    return dict(zip(('loja', 'codForn', 'serie', 'numero'), m.groups()))


def montar_rascunho(g, s, peso_par=0.7, peso_caixa=0.2):
    """g: {'loja', 'cnpjLoja', 'cnpjFornecedor', 'fornecedor', 'transportadora': {'cnpj'}|None, 'casos': [...]}.

    Devolve {'valor', 'danfe' (bytes), 'avisos'}. Levanta Parada (negócio) ou ErroIntegracao (rede/ERP).
    """
    avisos = []
    chk = lambda c: 'chk_' + c['erp']['codigoDefeito'].replace('.', '_')

    # 0. Rascunho pendente do mesmo fornecedor: não cria outro nem mexe no existente.
    antes = contar_pendentes(s.abrir('DevolFornecedor?action=cons'))
    if antes:
        pend = ler_pendentes(s.abrir('modEstoque/devolFornecedor/continuaDevolucao.jsp'))
        if any(normal(p['fornecedor']) == normal(g['fornecedor']) for p in pend):
            raise Parada(f"já existe rascunho de NFD pendente de {g['fornecedor']} no MACLE; não criei outro")

    por_nf = {}
    for c in g['casos']:
        d = partes_documento(c['erp']['nf']['documento'])
        if d['loja'] != g['loja']:
            raise Parada(f"caso {c['protocolo']}: NF de origem de outra loja ({c['erp']['nf']['documento']})")
        por_nf.setdefault(c['erp']['nf']['documento'], []).append(c)

    # 1. Nova + Enter (o Enter envia o formulário como está, com action=add).
    try:
        f_add = campos_form(s.abrir('DevolFornecedor?action=add&resY=897'), 'frmLiquidaReceber')
    except ValueError:
        raise Parada('tela de nova devolução não reconhecida')
    if f_add.get('action') != 'add':
        raise Parada(f"tela de nova devolução em estado inesperado ({f_add.get('action')}): há documento aberto na sessão?")
    if not re.search(r'name=["\']?action["\']?\s+value=["\']?origem', s.enviar('DevolFornecedor', f_add), re.I):
        raise Parada('Enter não abriu o documento')

    cod_forn = partes_documento(g['casos'][0]['erp']['nf']['documento'])['codForn']
    incluidos = []
    for i, (documento, casos) in enumerate(por_nf.items()):
        d = partes_documento(documento)
        # 2. Fornecedor + tipo (1ª NF) ou "Adicionar mais itens" (demais).
        if i == 0:
            s.abrir('DevolFornecedor?action=consOrigemWS')
            tela = s.abrir(f'DevolFornecedor?action=setTipoOperacaoDevolForn&codFornFilter={cod_forn}&tipoOperacao=MERCADORIA')
        else:
            s.abrir('DevolFornecedor?action=cad&doAction=origem&cleanOBS=true&infAdicional=')
            tela = s.abrir('DevolFornecedor?action=consOrigemWS')
        for c in casos:
            if chk(c) not in tela:
                raise Parada(f"defeito {c['erp']['codigoDefeito']} não aparece na devolução (já usado ou de outro fornecedor)")

        # 4. Marca o defeito (junto com os já incluídos, como a tela envia).
        f_def = {'action': 'defeito', 'codFornFilter': cod_forn, 'Y': '0', **{k: 'ativo' for k in incluidos + [chk(casos[0])]}, 'codigo': ''}
        if not re.search(rf"\b{d['numero']}\b", s.enviar('DevolFornecedor', f_def)):
            raise Parada(f'NF {documento} não aparece entre as notas de origem')

        # 5a. NF de origem.  5b. OK com os defeitos desta NF.
        c0 = casos[0]['erp']
        emp_def, num_def = c0['codigoDefeito'].split('.')
        emp_item, cod_item = c0['item'].split('.')
        tela = s.enviar('DevolFornecedor', {
            'action': 'setOrigem', 'codEmpresa': d['loja'], 'codFornecedor': d['codForn'], 'serie': d['serie'], 'numero': d['numero'],
            'codEmpDef': emp_def, 'codDefeito': num_def, 'codEmpItem': emp_item, 'codItem': cod_item, 'codTamanho': c0['tamanho'],
            'tipo': '3', 'uf': '', 'data': '', 'cnpj': '', 'chaveNFe': '', 'defeito': 'true'})
        for c in casos:
            if chk(c) not in tela:
                raise Parada(f"defeito {c['erp']['codigoDefeito']} não aparece entre os defeitos da NF {documento}")
        resp = s.enviar('DevolFornecedor', {'action': 'defeitoByCompra', 'codEmpOrig': d['loja'], 'origemOrig': d['codForn'],
                                            'serieOrig': d['serie'], 'numeroOrig': d['numero'], 'defeito': 'true', 'Y': '0',
                                            **{chk(c): 'ativo' for c in casos}})
        prox = destino(resp)
        if not prox:
            raise Parada('resposta do OK não reconhecida')

        # 6. Item de origem: a tela só aparece quando o ERP não casou o item com a linha da NF sozinho.
        fechou = False
        for _ in range(len(casos) + 3):
            pg = s.abrir(prox)
            tela = ler_item_origem(pg)
            if tela['associar'] and tela['linhas']:
                caso = next((c for c in casos if tela['codigo'] == f"{c['erp']['item']}.{c['erp']['tamanho']}"), casos[0])
                linha, etapas = escolher_linha(tela['linhas'], caso['erp']['referencia'], caso['erp']['tamanho'], caso['erp']['cor'])
                if not linha:
                    raise Parada(f"item de origem ambíguo para o caso {caso['protocolo']} na NF {documento}: {' -> '.join(etapas)}")
                depois = s.abrir(f"{tela['associar'][0]}{linha['nitem']}{tela['associar'][1]}")
                if re.search(r'Todos os itens foram associados', texto(depois), re.I):
                    falta = [c for c in casos if f"{c['erp']['item']}.{c['erp']['tamanho']}" not in texto(depois)]
                    if falta:
                        raise Parada('associação não confere: ' + ', '.join(c['protocolo'] for c in falta))
                    prox = tela['confirmar']
                else:
                    prox = 'DevolFornecedor?action=fechamto&offset=0'
                continue
            if no_fechamento(pg):
                fechou = True
                break
            prox = destino(pg)
            if not prox:
                raise Parada('tela depois do OK não reconhecida')
        if not fechou:
            raise Parada('não chegou ao fechamento depois de associar os itens')
        incluidos += [chk(c) for c in casos]

    # 7. Transporte.
    pares = sum(c['quantidade'] for c in g['casos'])
    p = pesos(pares, peso_par, peso_caixa)
    fecha = s.abrir('DevolFornecedor?action=consFechamto')
    transporte_cnpj = None
    if g.get('transportadora'):
        bt = re.search(r"'(modCaixa/cadDadosTransporte\.jsp\?[^']*resY=)'\s*\+\s*resY\s*\+\s*'([^']*)'", fecha)
        if not bt:
            raise Parada('botão Dados transporte não reconhecido')
        tela_t = s.abrir(f'{bt.group(1)}897{bt.group(2)}')
        alvo = so_digitos(g['transportadora']['cnpj'])
        opcoes = [o for o in ler_transportadoras(tela_t) if o['cnpj'] == alvo]
        if len(opcoes) != 1:
            raise Parada(f"transportadora {g['transportadora']['cnpj']} não encontrada (ou repetida) no MACLE")
        dados = s.abrir(f"Transportadora?action=selecionaTransportadora&codTransp={opcoes[0]['codigo']}").strip().split(';')
        if len(dados) < 9 or so_digitos(dados[1]) != alvo:
            raise Parada('cadastro da transportadora no MACLE não confere')
        f = campos_form(tela_t)
        f.update(action='save', selectTransp=opcoes[0]['codigo'], codTransportadora=dados[8].strip(), fretePorConta='2',
                 especie='CAIXA', quantidade='1', pesoLiquido=p['liquido'], pesoBruto=p['bruto'],
                 **dict(zip(['transportadora', 'cnpjTransp', 'inscEstTransp', 'endereco', 'bairro', 'cidade', 'codUF', 'codCEP'], dados)))
        fecha = s.enviar('DadosTransp', f)  # segue na página que o save devolve (como o navegador)
        transporte_cnpj = alvo
    else:
        avisos.append('fornecedor sem transportadora cadastrada: NFD sem transporte')

    # 8. Prévia do DANFE e conferência.
    if not re.search(r'name=["\']?obs\b', fecha, re.I):
        fecha = s.abrir('DevolFornecedor?action=consFechamto')
    obs, inf = textarea_bruto(fecha, 'obs') or '', campo(fecha, 'infAdicional') or ''
    pdf_url = re.search(r'tmp/\d+\.pdf', s.abrir(f'NFe?action=previewDANFE&tipo=devolForn&obs={quote(obs)}&rndval={int(time.time() * 1000)}'))
    if not pdf_url:
        raise Parada('prévia do DANFE não gerada')
    danfe = s.baixar(pdf_url.group(0))
    if not danfe.startswith(b'%PDF-'):
        raise ErroIntegracao('MACLE', 'prévia do DANFE não é PDF', temporaria=False)
    txt = texto_pdf(danfe)
    itens = [f"{c['erp']['item']}.{c['erp']['tamanho']}" for c in g['casos'] for _ in range(c['quantidade'])]
    total = round(sum(c['erp']['custo'] * c['quantidade'] for c in g['casos']), 2)
    div = conferir_danfe(txt, g['cnpjLoja'], g['cnpjFornecedor'], itens, total, transporte_cnpj, p if transporte_cnpj else None)
    if div:
        raise Parada('prévia do DANFE divergente, rascunho não salvo: ' + '; '.join(div))

    # 9. Gravar e sair.
    s.enviar('DevolFornecedor', {'action': 'continuarDepois', 'infAdicional': inf, 'obs': obs})
    depois = contar_pendentes(s.abrir('DevolFornecedor?action=cons'))
    if depois != antes + 1:
        avisos.append(f'lista de rascunhos pendentes foi de {antes} para {depois}; confira no MACLE')
    return {'valor': valor_da_nota(txt), 'danfe': danfe, 'avisos': avisos}
