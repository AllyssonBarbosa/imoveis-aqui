"""Testes da W12 — etapa 7: relatórios (critério 6).

Só o gestor vê, e só os dados da empresa dele. Datas fixas: o relatório de
parcelas recebe o "hoje" direto, e os contatos têm data de criação fixa.
"""

import shutil
import tempfile
from datetime import date, datetime, timezone as tz
from decimal import Decimal
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from contatos.models import Contato
from contratos.models import Baixa
from contratos.servicos import assinar_contrato, registrar_baixa, salvar_contrato
from contratos.tests import ONTEM, CenarioContrato
from contratos.tests_arquivos import gerar_pdf
from imoveis.models import Imovel
from imoveis.tests import criar_imovel
from imoveis.vitrine import FiltroInvalido

from .servicos import acervo_por_natureza_e_situacao, contatos_por_corretor, parcelas_em_atraso_e_a_vencer


class RelatoriosBase(CenarioContrato):
    """Empresa A:
    - casa VENDA-1 vendida (contrato de 01/06/2026: parcelas jul, ago e set/2026; a 1ª paga);
    - ALUGA-1 alugado (locação de 2027, 1º aluguel em 05/02/2027);
    - um terreno reservado e um apartamento disponível.
    Empresa B: um imóvel disponível e um contrato com parcelas vencidas — nada disso pode aparecer.
    """

    def setUp(self):
        pasta = tempfile.mkdtemp()
        configuracao = override_settings(ARQUIVOS_PRIVADOS_ROOT=Path(pasta))
        configuracao.enable()
        self.addCleanup(configuracao.disable)
        self.addCleanup(shutil.rmtree, pasta, ignore_errors=True)
        self.montar_cenario()

        self.venda = assinar_contrato(
            salvar_contrato(self.novo_contrato(data_inicio=date(2026, 6, 1)), self.gestor), self.gestor, ONTEM
        )
        registrar_baixa(
            Baixa(parcela=self.venda.parcelas.get(numero=1), data_pagamento=ONTEM, valor_pago=Decimal("30000.00"),
                  forma=Baixa.Forma.PIX, comprovante=gerar_pdf()),
            self.gestor,
        )
        self.locacao = assinar_contrato(salvar_contrato(self.nova_locacao(), self.gestor), self.gestor, ONTEM)

        proprietario = self.casa.proprietario
        criar_imovel(self.empresa, self.corretor, proprietario, codigo="TERRENO-1",
                     natureza=Imovel.Natureza.TERRENO, situacao=Imovel.Situacao.RESERVADO)
        criar_imovel(self.empresa, self.corretor, proprietario, codigo="APTO-1",
                     natureza=Imovel.Natureza.APARTAMENTO)

        contrato_b = salvar_contrato(
            self.novo_contrato(empresa=self.outra, imovel=self.imovel_b, cliente=self.cliente_b,
                               corretor=self.corretor_b, data_inicio=date(2026, 6, 1)),
            self.gestor_b,
        )
        self.contrato_b = assinar_contrato(contrato_b, self.gestor_b, ONTEM)
        criar_imovel(self.outra, self.corretor_b, self.imovel_b.proprietario, codigo="OUTRA-2")

    def contato(self, corretor, quando, empresa=None, imovel=None, retorno=Contato.Retorno.PENDENTE):
        contato = Contato.objects.create(
            empresa=empresa or self.empresa, imovel=imovel or self.casa, corretor=corretor,
            canal=Contato.Canal.WHATSAPP, retorno=retorno,
        )
        Contato.objects.filter(pk=contato.pk).update(criado_em=quando)  # criado_em é auto_now_add
        return contato


class AcervoTests(RelatoriosBase, TestCase):
    def test_conta_por_natureza_e_situacao_so_da_empresa(self):
        relatorio = acervo_por_natureza_e_situacao(self.empresa.pk)
        linhas = {linha["natureza"]: linha for linha in relatorio["linhas"]}
        self.assertEqual(linhas["CASA"]["por_situacao"],
                         {"DISPONIVEL": 0, "RESERVADO": 0, "VENDIDO": 1, "ALUGADO": 1})
        self.assertEqual(linhas["TERRENO"]["por_situacao"]["RESERVADO"], 1)
        self.assertEqual(linhas["APARTAMENTO"]["por_situacao"]["DISPONIVEL"], 1)
        self.assertEqual(linhas["LOTE"]["total"], 0)  # natureza sem imóvel aparece com zero
        self.assertEqual(relatorio["totais"], {"DISPONIVEL": 1, "RESERVADO": 1, "VENDIDO": 1, "ALUGADO": 1})
        self.assertEqual(relatorio["total"], 4)  # os 2 imóveis da empresa B não entram

    def test_outra_empresa_ve_so_o_dela(self):
        relatorio = acervo_por_natureza_e_situacao(self.outra.pk)
        self.assertEqual(relatorio["total"], 2)
        self.assertEqual(relatorio["totais"]["VENDIDO"], 1)


class ContatosTests(RelatoriosBase, TestCase):
    def setUp(self):
        super().setUp()
        self.contato(self.corretor, datetime(2027, 2, 10, 15, 0, tzinfo=tz.utc))
        self.contato(self.corretor, datetime(2027, 2, 20, 15, 0, tzinfo=tz.utc), retorno=Contato.Retorno.FALEI)
        # 01/03/2027 02:00 UTC = 28/02/2027 23:00 em São Paulo: conta em fevereiro
        self.contato(self.corretor2, datetime(2027, 3, 1, 2, 0, tzinfo=tz.utc))
        self.contato(self.corretor2, datetime(2027, 3, 15, 15, 0, tzinfo=tz.utc))
        self.contato(None, datetime(2027, 2, 12, 15, 0, tzinfo=tz.utc))  # foi para a empresa
        self.contato(self.corretor_b, datetime(2027, 2, 12, 15, 0, tzinfo=tz.utc), empresa=self.outra,
                     imovel=self.imovel_b)

    def por_corretor(self, params):
        relatorio = contatos_por_corretor(self.empresa.pk, params)
        return {linha["corretor_id"]: (linha["total"], linha["pendentes"]) for linha in relatorio["linhas"]}, relatorio

    def test_todo_o_periodo(self):
        linhas, relatorio = self.por_corretor({})
        self.assertEqual(linhas, {self.corretor.pk: (2, 1), self.corretor2.pk: (2, 2), None: (1, 1)})
        self.assertEqual((relatorio["total"], relatorio["pendentes"]), (5, 4))

    def test_periodo_usa_o_dia_de_sao_paulo(self):
        linhas, _ = self.por_corretor({"de": "2027-02-01", "ate": "2027-02-28"})
        self.assertEqual(linhas, {self.corretor.pk: (2, 1), self.corretor2.pk: (1, 1), None: (1, 1)})

    def test_corretor_ativo_sem_contato_aparece_com_zero(self):
        linhas, _ = self.por_corretor({"de": "2027-03-02", "ate": "2027-03-31"})
        self.assertEqual(linhas[self.corretor.pk], (0, 0))
        self.assertEqual(linhas[self.corretor2.pk], (1, 1))
        self.assertNotIn(None, linhas)

    def test_quem_recebeu_mais_vem_primeiro(self):
        self.contato(self.corretor2, datetime(2027, 3, 20, 15, 0, tzinfo=tz.utc))
        _, relatorio = self.por_corretor({})
        self.assertEqual(relatorio["linhas"][0]["corretor_id"], self.corretor2.pk)

    def test_corretor_desativado_com_contato_continua_no_relatorio(self):
        self.corretor.is_active = False
        self.corretor.save()
        linhas, _ = self.por_corretor({})
        self.assertEqual(linhas[self.corretor.pk], (2, 1))

    def test_data_invalida_ou_invertida(self):
        for params in ({"de": "10/02/2027"}, {"de": "2027-03-01", "ate": "2027-02-01"}):
            with self.subTest(params=params), self.assertRaises(FiltroInvalido):
                contatos_por_corretor(self.empresa.pk, params)


class ParcelasTests(RelatoriosBase, TestCase):
    HOJE = date(2027, 1, 20)

    def test_em_atraso_e_a_vencer(self):
        relatorio = parcelas_em_atraso_e_a_vencer(self.empresa.pk, dias=30, hoje=self.HOJE)
        # venda: 1ª paga; 2ª (31/08/2026) e 3ª (30/09/2026) atrasadas
        self.assertEqual([(p.contrato_id, p.numero) for p in relatorio["em_atraso"]],
                         [(self.venda.pk, 2), (self.venda.pk, 3)])
        self.assertEqual(relatorio["saldo_em_atraso"], Decimal("60000.00"))
        self.assertEqual([p.dias_em_atraso for p in relatorio["em_atraso"]], [142, 112])
        # locação: 1º aluguel em 05/02/2027, dentro dos 30 dias
        self.assertEqual([(p.contrato_id, p.numero) for p in relatorio["a_vencer"]], [(self.locacao.pk, 1)])
        self.assertEqual(relatorio["saldo_a_vencer"], Decimal("2500.00"))

    def test_limite_de_dias_inclui_o_ultimo_dia(self):
        # 20/01 + 16 dias = 05/02 (entra); + 15 dias = 04/02 (não entra)
        self.assertEqual(len(parcelas_em_atraso_e_a_vencer(self.empresa.pk, dias=16, hoje=self.HOJE)["a_vencer"]), 1)
        self.assertEqual(len(parcelas_em_atraso_e_a_vencer(self.empresa.pk, dias=15, hoje=self.HOJE)["a_vencer"]), 0)

    def test_contrato_em_rascunho_nao_entra(self):
        salvar_contrato(  # rascunho com parcelas já "vencidas": não é cobrança ainda
            self.nova_locacao(data_inicio=date(2026, 1, 1), data_fim=date(2026, 12, 31)), self.gestor
        )
        relatorio = parcelas_em_atraso_e_a_vencer(self.empresa.pk, dias=365, hoje=self.HOJE)
        contratos = {p.contrato_id for p in relatorio["em_atraso"] + relatorio["a_vencer"]}
        self.assertEqual(contratos, {self.venda.pk, self.locacao.pk})

    def test_outra_empresa_nao_aparece(self):
        relatorio = parcelas_em_atraso_e_a_vencer(self.empresa.pk, dias=365, hoje=self.HOJE)
        self.assertNotIn(self.contrato_b.pk, {p.contrato_id for p in relatorio["em_atraso"]})
        relatorio_b = parcelas_em_atraso_e_a_vencer(self.outra.pk, dias=365, hoje=self.HOJE)
        self.assertEqual({p.contrato_id for p in relatorio_b["em_atraso"]}, {self.contrato_b.pk})


class PainelRelatoriosTests(RelatoriosBase, TestCase):
    URLS = ["relatorios:index", "relatorios:acervo", "relatorios:contatos", "relatorios:parcelas"]

    def test_gestor_ve_todos(self):
        self.client.force_login(self.gestor)
        for nome in self.URLS:
            with self.subTest(url=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)

    def test_corretor_e_administrador_nao_veem(self):
        for usuario in (self.corretor, self.admin):
            self.client.force_login(usuario)
            for nome in self.URLS:
                with self.subTest(usuario=usuario.email, url=nome):
                    self.assertEqual(self.client.get(reverse(nome)).status_code, 403)

    def test_anonimo_vai_para_o_login(self):
        response = self.client.get(reverse("relatorios:acervo"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("contas:login"), response["Location"])

    def test_tela_de_parcelas_mostra_atrasadas_da_empresa(self):
        self.client.force_login(self.gestor)
        response = self.client.get(reverse("relatorios:parcelas"), {"dias": "365"})
        self.assertContains(response, "Em atraso (2)")
        self.assertContains(response, reverse("contratos:parcela_detail",
                                              args=[self.venda.pk, self.venda.parcelas.get(numero=2).pk]))
        self.assertNotContains(response, f">#{self.contrato_b.pk}</a>")  # contrato da empresa B

    def test_erros_de_filtro_aparecem_na_tela(self):
        self.client.force_login(self.gestor)
        self.assertContains(self.client.get(reverse("relatorios:parcelas"), {"dias": "0"}), "entre 1 e 365")
        self.assertContains(self.client.get(reverse("relatorios:contatos"), {"de": "ontem"}), "AAAA-MM-DD")

    def test_acervo_na_tela(self):
        self.client.force_login(self.gestor)
        response = self.client.get(reverse("relatorios:acervo"))
        self.assertContains(response, "Apartamento")
        self.assertContains(response, "<th>4</th>", html=False)

    def test_painel_do_gestor_tem_o_link(self):
        self.client.force_login(self.gestor)
        self.assertContains(self.client.get(reverse("contas:painel")), reverse("relatorios:index"))


class ApiRelatoriosTests(RelatoriosBase, APITestCase):
    URLS = ["relatorio-acervo", "relatorio-contatos", "relatorio-parcelas"]

    def test_so_o_gestor(self):
        casos = [(self.corretor, 403), (self.admin, 403), (None, 401), (self.gestor, 200)]
        for usuario, esperado in casos:
            self.client.force_authenticate(usuario)
            for nome in self.URLS:
                with self.subTest(usuario=getattr(usuario, "email", None), url=nome):
                    self.assertEqual(self.client.get(reverse(nome)).status_code, esperado)

    def test_acervo(self):
        self.client.force_authenticate(self.gestor_b)
        self.assertEqual(self.client.get(reverse("relatorio-acervo")).data["total"], 2)

    def test_parcelas_com_dinheiro_em_texto_e_so_da_empresa(self):
        self.client.force_authenticate(self.gestor)
        dados = self.client.get(reverse("relatorio-parcelas"), {"dias": "365"}).data
        self.assertEqual({p["contrato"] for p in dados["em_atraso"]}, {self.venda.pk})
        self.assertEqual(dados["saldo_em_atraso"], "60000.00")
        self.assertEqual(dados["em_atraso"][0]["saldo"], "30000.00")
        self.assertGreater(dados["em_atraso"][0]["dias_em_atraso"], 0)
        self.assertNotIn("documento", str(dados))  # nada de CPF do cliente

    def test_filtros_invalidos_dao_400(self):
        self.client.force_authenticate(self.gestor)
        self.assertEqual(self.client.get(reverse("relatorio-parcelas"), {"dias": "abc"}).status_code, 400)
        self.assertEqual(
            self.client.get(reverse("relatorio-contatos"), {"de": "2027-03-01", "ate": "2027-02-01"}).status_code,
            400,
        )

    def test_contatos(self):
        self.contato(self.corretor, datetime(2027, 2, 10, 15, 0, tzinfo=tz.utc))
        self.client.force_authenticate(self.gestor)
        dados = self.client.get(reverse("relatorio-contatos"), {"de": "2027-02-01", "ate": "2027-02-28"}).data
        self.assertEqual(dados["total"], 1)
        self.assertEqual(dados["linhas"][0]["corretor_id"], self.corretor.pk)
