"""Testes da W12 — etapa 6: parcela atrasada calculada pela data (critério 5).

A regra: parcela de contrato assinado que venceu ANTES de hoje e ainda tem
saldo está atrasada. Vencer hoje ainda não é atraso. Nada disso é gravado:
é calculado na consulta. As datas são fixas — o "hoje" é passado direto, ou
o relógio é congelado num instante conhecido para testar o fuso de São Paulo.
"""

import shutil
import tempfile
from datetime import date, datetime, timezone as tz
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Baixa, Parcela
from .servicos import assinar_contrato, registrar_baixa, salvar_contrato
from .tests import ONTEM, CenarioContrato
from .tests_arquivos import gerar_pdf

S = Parcela.Situacao

# contrato que começa em 10/12/2026: 3x de R$ 30.000 vencendo todo dia 31 a partir do mês seguinte
# (31/01, 28/02 — fevereiro não tem 31 — e 31/03/2027)
INICIO = date(2026, 12, 10)
VENCE_1, VENCE_2, VENCE_3 = date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)


class AtrasoBase(CenarioContrato):
    def setUp(self):
        pasta = tempfile.mkdtemp()
        configuracao = override_settings(ARQUIVOS_PRIVADOS_ROOT=Path(pasta))
        configuracao.enable()
        self.addCleanup(configuracao.disable)
        self.addCleanup(shutil.rmtree, pasta, ignore_errors=True)
        self.montar_cenario()

    def contrato_assinado(self, **extra):
        return assinar_contrato(salvar_contrato(self.novo_contrato(**extra), self.gestor), self.gestor, ONTEM)

    def pagar(self, parcela, valor):
        registrar_baixa(
            Baixa(parcela=parcela, data_pagamento=ONTEM, valor_pago=Decimal(valor), forma=Baixa.Forma.PIX,
                  comprovante=gerar_pdf()),
            self.gestor,
        )

    def situacoes(self, contrato, hoje=None):
        return [p.situacao for p in contrato.parcelas.com_situacao(hoje).order_by("numero")]


class SituacaoDaParcelaTests(AtrasoBase, TestCase):
    def setUp(self):
        super().setUp()
        self.contrato = self.contrato_assinado(data_inicio=INICIO)
        self.p1, self.p2, self.p3 = self.contrato.parcelas.order_by("numero")

    def test_vencimentos_do_cenario(self):
        self.assertEqual([self.p1.vencimento, self.p2.vencimento, self.p3.vencimento], [VENCE_1, VENCE_2, VENCE_3])

    def test_antes_de_tudo_vencer_todas_a_vencer(self):
        self.assertEqual(self.situacoes(self.contrato, date(2027, 1, 15)), [S.A_VENCER] * 3)

    def test_vencer_hoje_ainda_nao_e_atraso(self):
        self.assertEqual(self.situacoes(self.contrato, VENCE_1), [S.A_VENCER] * 3)

    def test_um_dia_depois_do_vencimento_esta_atrasada(self):
        self.assertEqual(self.situacoes(self.contrato, date(2027, 2, 1)), [S.ATRASADA, S.A_VENCER, S.A_VENCER])

    def test_varias_atrasadas(self):
        self.assertEqual(
            self.situacoes(self.contrato, date(2027, 4, 1)), [S.ATRASADA, S.ATRASADA, S.ATRASADA]
        )

    def test_pagamento_parcial_continua_atrasada(self):
        self.pagar(self.p1, "29999.99")
        self.assertEqual(self.situacoes(self.contrato, date(2027, 2, 1))[0], S.ATRASADA)

    def test_pagamento_completo_em_partes_fica_paga(self):
        self.pagar(self.p1, "10000.00")
        self.pagar(self.p1, "20000.00")
        self.assertEqual(self.situacoes(self.contrato, date(2027, 4, 1)), [S.PAGA, S.ATRASADA, S.ATRASADA])

    def test_pagamento_a_mais_fica_paga(self):
        self.pagar(self.p2, "31000.00")
        self.assertEqual(self.situacoes(self.contrato, date(2027, 4, 1))[1], S.PAGA)

    def test_paga_antes_do_vencimento_nunca_fica_atrasada(self):
        self.pagar(self.p3, "30000.00")
        for hoje in (date(2027, 1, 1), VENCE_3, date(2030, 1, 1)):
            with self.subTest(hoje=hoje):
                self.assertEqual(self.situacoes(self.contrato, hoje)[2], S.PAGA)

    def test_contrato_em_rascunho_e_previsto_nunca_atrasado(self):
        rascunho = salvar_contrato(self.novo_contrato(), self.gestor)
        self.assertEqual(self.situacoes(rascunho, date(2030, 1, 1)), [S.PREVISTA] * 3)

    def test_dias_em_atraso_contados_ate_o_mesmo_hoje(self):
        parcelas = list(self.contrato.parcelas.com_situacao(date(2027, 3, 1)).order_by("numero"))
        self.assertEqual([p.dias_em_atraso for p in parcelas], [29, 1, 0])

    def test_filtros_de_atrasadas_e_a_vencer(self):
        hoje = date(2027, 3, 1)
        self.assertEqual(
            list(self.contrato.parcelas.atrasadas(hoje).order_by("numero").values_list("numero", flat=True)), [1, 2]
        )
        self.assertEqual(list(self.contrato.parcelas.a_vencer(hoje).values_list("numero", flat=True)), [3])

    def test_situacao_nao_e_gravada_no_banco(self):
        colunas = {campo.name for campo in Parcela._meta.get_fields()}
        self.assertFalse(colunas & {"situacao", "atrasada", "paga", "status"})

    def test_mesma_parcela_muda_de_situacao_so_com_a_data(self):
        """Nenhuma tarefa agendada nem gravação: a mesma linha do banco, consultada em dias diferentes."""
        antes = self.contrato.parcelas.com_situacao(VENCE_1).get(numero=1)
        depois = self.contrato.parcelas.com_situacao(date(2027, 2, 1)).get(numero=1)
        self.assertEqual((antes.situacao, depois.situacao), (S.A_VENCER, S.ATRASADA))


class FusoDeSaoPauloTests(AtrasoBase, TestCase):
    """O "hoje" é o dia em São Paulo, não em UTC (o servidor guarda as horas em UTC)."""

    def setUp(self):
        super().setUp()
        self.contrato = self.contrato_assinado(data_inicio=INICIO)

    def situacao_no_instante(self, instante_utc):
        with mock.patch("django.utils.timezone.now", return_value=instante_utc):
            return self.contrato.parcelas.com_situacao().get(numero=1).situacao

    def test_23h_em_sao_paulo_do_dia_do_vencimento_ainda_nao_e_atraso(self):
        # 01/02/2027 01:59 UTC = 31/01/2027 22:59 em São Paulo (UTC−3): ainda é o dia do vencimento
        self.assertEqual(self.situacao_no_instante(datetime(2027, 2, 1, 1, 59, tzinfo=tz.utc)), S.A_VENCER)

    def test_meia_noite_em_sao_paulo_vira_atraso(self):
        # 01/02/2027 03:00 UTC = 01/02/2027 00:00 em São Paulo
        self.assertEqual(self.situacao_no_instante(datetime(2027, 2, 1, 3, 0, tzinfo=tz.utc)), S.ATRASADA)


class PainelAtrasoTests(AtrasoBase, TestCase):
    """Contrato que começou em 01/06/2026: as 3 parcelas (jul, ago e set/2026) já venceram."""

    def setUp(self):
        super().setUp()
        self.contrato = self.contrato_assinado(data_inicio=date(2026, 6, 1))
        self.p1 = self.contrato.parcelas.get(numero=1)
        self.pagar(self.p1, "30000.00")

    def test_detalhe_mostra_atrasadas_e_o_saldo_vencido(self):
        self.client.force_login(self.gestor)
        response = self.client.get(reverse("contratos:contrato_detail", args=[self.contrato.pk]))
        self.assertContains(response, "2 parcelas em atraso")
        self.assertContains(response, "60.000,00")
        self.assertContains(response, "parcela-atrasada", count=2)
        self.assertContains(response, "parcela-paga", count=1)

    def test_tela_da_parcela_mostra_dias_em_atraso(self):
        p2 = self.contrato.parcelas.get(numero=2)
        self.client.force_login(self.corretor)
        response = self.client.get(reverse("contratos:parcela_detail", args=[self.contrato.pk, p2.pk]))
        self.assertContains(response, "Atrasada há")


class ApiAtrasoTests(AtrasoBase, APITestCase):
    def setUp(self):
        super().setUp()
        self.vencido = self.contrato_assinado(data_inicio=date(2026, 6, 1))  # 3 parcelas vencidas
        self.locacao_futura = assinar_contrato(  # do corretor 2, tudo a vencer
            salvar_contrato(self.nova_locacao(corretor=self.corretor2), self.gestor), self.gestor, ONTEM
        )

    def test_contrato_traz_situacao_calculada_de_cada_parcela(self):
        self.client.force_authenticate(self.gestor)
        parcelas = self.client.get(reverse("contrato-detail", args=[self.vencido.pk])).data["parcelas"]
        self.assertEqual({p["situacao"] for p in parcelas}, {"ATRASADA"})
        self.assertEqual(parcelas[0]["situacao_rotulo"], "Atrasada")
        self.assertGreater(parcelas[0]["dias_em_atraso"], 0)

    def test_lista_de_parcelas_filtra_por_situacao(self):
        self.client.force_authenticate(self.gestor)
        atrasadas = self.client.get(reverse("parcela-list"), {"situacao": "ATRASADA"}).data
        self.assertEqual({p["contrato"] for p in atrasadas}, {self.vencido.pk})
        self.assertEqual(len(atrasadas), 3)
        a_vencer = self.client.get(reverse("parcela-list"), {"situacao": "A_VENCER"}).data
        self.assertEqual({p["contrato"] for p in a_vencer}, {self.locacao_futura.pk})

    def test_situacao_invalida_da_400(self):
        self.client.force_authenticate(self.gestor)
        response = self.client.get(reverse("parcela-list"), {"situacao": "QUALQUER"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_isolamento_corretor_e_outra_empresa(self):
        self.client.force_authenticate(self.corretor2)
        contratos = {p["contrato"] for p in self.client.get(reverse("parcela-list")).data}
        self.assertEqual(contratos, {self.locacao_futura.pk})
        self.client.force_authenticate(self.gestor_b)
        self.assertEqual(self.client.get(reverse("parcela-list")).data, [])
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(reverse("parcela-list")).status_code, status.HTTP_403_FORBIDDEN)

    def test_parcela_nao_e_editavel(self):
        parcela = self.vencido.parcelas.first()
        self.client.force_authenticate(self.gestor)
        url = reverse("parcela-detail", args=[parcela.pk])
        self.assertEqual(self.client.patch(url, {"valor": "1.00"}).status_code, 405)
