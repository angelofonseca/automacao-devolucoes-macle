"""Ponto de entrada (gatilho).

  python -m defeitos.main --once   uma rodada e sai (demo, cron do sistema, Agendador do Windows)
  python -m defeitos.main          roda para sempre, uma rodada a cada INTERVALO_MIN minutos

Uma falha inesperada numa rodada é registrada no log e avisada no Telegram; o agendamento continua.
"""
import argparse
import sys
import time
import traceback

from . import config, pipeline, telegram
from .resiliencia import esconder, registrar


def rodar(cfg):
    """Uma rodada protegida: nunca levanta exceção; devolve True se terminou sem falhas."""
    inicio = time.monotonic()
    try:
        r = pipeline.executar(cfg)
    except Exception as exc:
        detalhe = esconder(f'{type(exc).__name__}: {exc}', cfg.erp_senha, cfg.gmail_senha, cfg.telegram_token)
        registrar(cfg.log, evento='rodada', ok=False, erro=detalhe,
                  traceback=esconder(traceback.format_exc(), cfg.erp_senha, cfg.gmail_senha, cfg.telegram_token))
        print(f'[ERRO] rodada falhou: {detalhe}', file=sys.stderr)
        try:
            telegram.enviar(cfg, f'🚨 Automação de defeitos: a rodada falhou\n{detalhe}')
        except Exception:
            pass  # sem Telegram, fica só o log
        return False
    registrar(cfg.log, evento='rodada', ok=r['ok'], duracao_s=round(time.monotonic() - inicio, 2),
              rascunhos=len(r['rascunhos']), aguardando=len(r['aguardando']), bloqueados=len(r['bloqueados']),
              invalidos=len(r['invalidos']), falhas=r['falhas'], avisou=r['avisou'])
    print(r['mensagem'])
    destino = {'telegram': 'enviado ao Telegram', 'arquivo': 'gravado em saida/telegram.log'}
    print(f"-> Resumo: {destino[r['telegram']['modo']] if r['avisou'] else 'nada novo, sem aviso'}")
    return r['ok']


def main(argv=None):
    for fluxo in (sys.stdout, sys.stderr):
        fluxo.reconfigure(encoding='utf-8')  # emojis no console do Windows
    parser = argparse.ArgumentParser(description='Automação de devoluções de produtos com defeito')
    parser.add_argument('--once', action='store_true', help='executa uma rodada e sai')
    args = parser.parse_args(argv)
    try:
        cfg = config.carregar()
    except config.ConfigInvalida as exc:
        print(f'[CONFIG] {exc}', file=sys.stderr)
        return 2
    if args.once:
        return 0 if rodar(cfg) else 1
    print(f'Agendado: uma rodada a cada {cfg.intervalo_min} min (Ctrl+C para parar)')
    while True:
        rodar(cfg)
        try:
            time.sleep(cfg.intervalo_min * 60)
        except KeyboardInterrupt:
            return 0


if __name__ == '__main__':
    raise SystemExit(main())
