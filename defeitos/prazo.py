"""Prazo de garantia da fábrica, contado da emissão da NF de compra.

Formatos aceitos em `prazo` do fornecedor:
  {"meses": 12}            12 meses da emissão
  {"dias": 180}            180 dias da emissão
  {"anosNf": [2025, 2026]} vale para NF emitida nesses anos
  null / sem meses e dias  padrão de 24 meses (fábrica não informou)
  "marco" diferente de emissão/faturamento -> indefinido (não dá para afirmar)
"""
from datetime import date, timedelta

PRAZO_PADRAO_MESES = 24


def data_br(texto):
    """'31/01/2026' -> date; qualquer coisa inválida -> None."""
    try:
        d, m, a = (int(x) for x in texto.split('/'))
        return date(a, m, d)
    except (AttributeError, ValueError):
        return None


def somar_meses(d, meses):
    """Soma meses cortando no último dia do mês (31/01 + 1 mês = 28 ou 29/02)."""
    m = d.month - 1 + meses
    ano, mes = d.year + m // 12, m % 12 + 1
    bissexto = ano % 4 == 0 and (ano % 100 != 0 or ano % 400 == 0)
    dias_no_mes = [31, 29 if bissexto else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mes - 1]
    return date(ano, mes, min(d.day, dias_no_mes))


def avaliar(prazo, emissao, hoje):
    """{'situacao': 'dentro' | 'fora' | 'indefinido', 'motivo': texto, 'vence': date | None}."""
    prazo = prazo or {}
    d = data_br(emissao)
    if prazo.get('anosNf'):
        if not d:
            return {'situacao': 'indefinido', 'motivo': 'NF sem data de emissão', 'vence': None}
        dentro = d.year in prazo['anosNf']
        return {'situacao': 'dentro' if dentro else 'fora', 'vence': None,
                'motivo': f"política cobre NF de {', '.join(map(str, prazo['anosNf']))}; NF de {emissao}"}
    nota = ''
    if not prazo.get('meses') and not prazo.get('dias'):
        prazo, nota = {'meses': PRAZO_PADRAO_MESES}, ' (prazo padrão)'
    elif prazo.get('marco') not in (None, 'nao_informado', 'emissao_nf', 'faturamento'):
        return {'situacao': 'indefinido', 'vence': None, 'motivo': f"prazo conta de '{prazo['marco']}', não da NF"}
    if not d:
        return {'situacao': 'indefinido', 'motivo': 'NF sem data de emissão', 'vence': None}
    if prazo.get('meses'):
        vence, texto = somar_meses(d, prazo['meses']), f"{prazo['meses']} meses"
    else:
        vence, texto = d + timedelta(days=prazo['dias']), f"{prazo['dias']} dias"
    return {'situacao': 'dentro' if hoje <= vence else 'fora', 'vence': vence,
            'motivo': f'{texto} da NF de {emissao}{nota}: vence em {vence:%d/%m/%Y}'}
