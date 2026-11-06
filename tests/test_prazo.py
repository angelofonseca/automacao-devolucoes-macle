import json
import unittest
from datetime import date
from pathlib import Path

from defeitos import prazo

CASOS = json.loads((Path(__file__).parent / 'fixtures' / 'prazo_casos.json').read_text(encoding='utf-8'))['casos']


class TestPrazo(unittest.TestCase):
    def test_casos_da_fixture(self):
        for c in CASOS:
            with self.subTest(c['nome']):
                r = prazo.avaliar(c['prazo'], c['emissao'], date.fromisoformat(c['hoje']))
                self.assertEqual(r['situacao'], c['esperado'], r['motivo'])

    def test_data_invalida(self):
        self.assertIsNone(prazo.data_br('31/02/2026'))
        self.assertIsNone(prazo.data_br(None))


if __name__ == '__main__':
    unittest.main()
