"""Testes da W12 — etapa 7: relatórios (critério 6).

Só o gestor vê, e só os dados da empresa dele. Datas fixas: o relatório de
parcelas recebe o "hoje" direto, e os contatos têm data de criação fixa.
"""

import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone as tz
from decimal import Decimal
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.timezone import localdate
from rest_framework import status
from rest_framework.test import APITestCase

from contas.models import Usuario
from contas.tests import fixar_taxa_alta_no_login
from contatos.models import Contato
from contratos.models import Baixa
from contratos.servicos import assinar_contrato, registrar_baixa, salvar_contrato
from contratos.tests import ONTEM, CenarioContrato
from contratos.tests_arquivos import gerar_pdf
from imoveis.models import HistoricoSituacao, Imovel
from imoveis.tests import criar_corretor, criar_imovel
from imoveis.vitrine import FiltroInvalido

from .servicos import (
    acervo_por_corretor, acervo_por_natureza_e_situacao, contatos_por_corretor, ler_dias, mudancas_de_situacao,
    parcelas_em_atraso_e_a_vencer,
)


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


class AcervoPorCorretorTests(RelatoriosBase, TestCase):
    """Acervo por corretor (critério 1 do card APP12; D-12 e D-13).

    Mesma base do acervo por natureza × situação: todos os imóveis da empresa, em qualquer
    situação, publicados ou não. Todo corretor ativo aparece, mesmo com zero; o desativado que
    ainda é responsável por imóvel aparece com `ativo` falso. Quem tem mais vem primeiro e, no
    empate, por nome. Nada de outra empresa entra.
    """

    def linhas(self, empresa_id=None):
        relatorio = acervo_por_corretor(empresa_id or self.empresa.pk)
        return {linha["corretor_id"]: linha for linha in relatorio["linhas"]}, relatorio

    def test_conta_por_corretor_e_corretor_ativo_sem_imovel_aparece_com_zero(self):
        linhas, relatorio = self.linhas()
        self.assertEqual(linhas[self.corretor.pk]["total"], 4)
        self.assertEqual(linhas[self.corretor2.pk]["total"], 0)  # ativo, sem imóvel: aparece com zero
        self.assertTrue(linhas[self.corretor.pk]["ativo"])
        self.assertEqual(relatorio["total"], 4)

    def test_cada_linha_tem_so_as_quatro_chaves(self):
        for linha in acervo_por_corretor(self.empresa.pk)["linhas"]:
            self.assertEqual(set(linha), {"corretor_id", "corretor", "ativo", "total"})

    def test_soma_das_linhas_fecha_com_o_total_do_acervo_por_natureza(self):
        relatorio = acervo_por_corretor(self.empresa.pk)
        self.assertEqual(sum(linha["total"] for linha in relatorio["linhas"]), relatorio["total"])
        self.assertEqual(relatorio["total"], acervo_por_natureza_e_situacao(self.empresa.pk)["total"])

    def test_imovel_nao_publicado_tambem_conta(self):
        criar_imovel(self.empresa, self.corretor2, self.casa.proprietario, codigo="RASCUNHO-1", publicado=False)
        linhas, relatorio = self.linhas()
        self.assertEqual(linhas[self.corretor2.pk]["total"], 1)
        self.assertEqual(relatorio["total"], 5)

    def test_gestor_sem_imovel_nao_aparece_e_outra_empresa_nunca(self):
        linhas, _ = self.linhas()
        self.assertNotIn(self.gestor.pk, linhas)
        self.assertNotIn(self.corretor_b.pk, linhas)
        self.assertEqual(set(linhas), {self.corretor.pk, self.corretor2.pk})

    def test_outra_empresa_ve_so_o_corretor_dela(self):
        linhas, relatorio = self.linhas(self.outra.pk)
        self.assertEqual(set(linhas), {self.corretor_b.pk})
        self.assertEqual(linhas[self.corretor_b.pk]["total"], 2)
        self.assertEqual(relatorio["total"], 2)

    def test_corretor_desativado_que_ainda_e_responsavel_aparece_como_inativo(self):
        antigo = criar_corretor(self.empresa, email="antigo@teste.com")
        criar_imovel(self.empresa, antigo, self.casa.proprietario, codigo="ANTIGO-1")
        criar_imovel(self.empresa, antigo, self.casa.proprietario, codigo="ANTIGO-2")
        Usuario.objects.filter(pk=antigo.pk).update(is_active=False)
        linhas, relatorio = self.linhas()
        self.assertFalse(linhas[antigo.pk]["ativo"])
        self.assertEqual(linhas[antigo.pk]["total"], 2)
        self.assertEqual(relatorio["total"], 6)

    def test_corretor_desativado_sem_imovel_nao_aparece(self):
        Usuario.objects.filter(pk=self.corretor2.pk).update(is_active=False)
        linhas, _ = self.linhas()
        self.assertNotIn(self.corretor2.pk, linhas)

    def test_quem_tem_mais_vem_primeiro_e_no_empate_por_nome(self):
        outro = criar_corretor(self.empresa, email="outro@teste.com")
        Usuario.objects.filter(pk=self.corretor2.pk).update(nome="Zeca")
        Usuario.objects.filter(pk=outro.pk).update(nome="Ana")
        criar_imovel(self.empresa, self.corretor2, self.casa.proprietario, codigo="Z-1")
        criar_imovel(self.empresa, outro, self.casa.proprietario, codigo="A-1")
        linhas = acervo_por_corretor(self.empresa.pk)["linhas"]
        self.assertEqual([linha["corretor_id"] for linha in linhas],
                         [self.corretor.pk, outro.pk, self.corretor2.pk])
        self.assertEqual([linha["total"] for linha in linhas], [4, 1, 1])


class MudancasDeSituacaoTests(RelatoriosBase, TestCase):
    """Mudanças de situação dos últimos dias (critério 3 do card APP12; D-19 e D-20).

    Janela inclusiva no dia de São Paulo: hoje e os `dias - 1` anteriores. Só o histórico de
    imóveis da empresa pedida; entram também as mudanças gravadas pela assinatura de contrato.
    """

    HOJE = date(2027, 1, 20)

    def mudanca(self, quando, observacao, imovel=None, usuario=None):
        """Grava uma mudança e fixa o `alterado_em` (o campo é auto_now_add)."""
        historico = HistoricoSituacao.objects.create(
            imovel=imovel or self.casa, situacao_anterior=Imovel.Situacao.DISPONIVEL,
            situacao_nova=Imovel.Situacao.RESERVADO, alterado_por=usuario or self.corretor,
            observacao=observacao,
        )
        HistoricoSituacao.objects.filter(pk=historico.pk).update(alterado_em=quando)
        return historico

    def observacoes(self, empresa_id=None, **extra):
        relatorio = mudancas_de_situacao(empresa_id or self.empresa.pk, hoje=self.HOJE, **extra)
        return [historico.observacao for historico in relatorio["mudancas"]]

    def test_ler_dias_sem_valor_continua_30_e_aceita_outro_padrao(self):
        self.assertEqual(ler_dias(""), 30)
        self.assertEqual(ler_dias(None), 30)
        self.assertEqual(ler_dias("", padrao=7), 7)
        self.assertEqual(ler_dias(None, padrao=7), 7)
        self.assertEqual(ler_dias("12", padrao=7), 12)

    def test_ler_dias_invalido_levanta_filtro_invalido_com_ou_sem_padrao(self):
        for valor in ("0", "366", "abc", "-1"):
            for extra in ({}, {"padrao": 7}):
                with self.subTest(valor=valor, extra=extra):
                    with self.assertRaises(FiltroInvalido) as contexto:
                        ler_dias(valor, **extra)
                    self.assertIn("dias", contexto.exception.erros)

    def test_janela_de_7_dias_conta_hoje_e_os_6_anteriores(self):
        relatorio = mudancas_de_situacao(self.empresa.pk, dias=7, hoje=self.HOJE)
        self.assertEqual(relatorio["dias"], 7)
        self.assertEqual(relatorio["desde"], date(2027, 1, 14))

    def test_padrao_de_dias_e_7(self):
        self.assertEqual(mudancas_de_situacao(self.empresa.pk, hoje=self.HOJE)["dias"], 7)

    def test_limites_da_janela_usam_o_dia_de_sao_paulo(self):
        self.mudanca(datetime(2027, 1, 14, 2, 30, tzinfo=tz.utc), "fora-13-23h30")   # 23:30 do dia 13 em SP
        self.mudanca(datetime(2027, 1, 14, 3, 30, tzinfo=tz.utc), "dentro-14-00h30")  # 00:30 do dia 14 em SP
        self.mudanca(datetime(2027, 1, 20, 15, 0, tzinfo=tz.utc), "dentro-20")
        self.mudanca(datetime(2027, 1, 21, 2, 30, tzinfo=tz.utc), "dentro-20-23h30")  # 23:30 do dia 20 em SP
        self.mudanca(datetime(2027, 1, 21, 3, 30, tzinfo=tz.utc), "fora-21-00h30")    # 00:30 do dia 21 em SP
        self.mudanca(datetime(2027, 1, 21, 15, 0, tzinfo=tz.utc), "fora-21")
        self.assertEqual(
            set(self.observacoes()), {"dentro-14-00h30", "dentro-20", "dentro-20-23h30"}
        )

    def test_dias_maior_alarga_a_janela(self):
        self.mudanca(datetime(2027, 1, 5, 15, 0, tzinfo=tz.utc), "dia-5")
        self.assertNotIn("dia-5", self.observacoes(dias=7))
        self.assertIn("dia-5", self.observacoes(dias=16))

    def test_so_as_mudancas_da_empresa_pedida(self):
        self.mudanca(datetime(2027, 1, 18, 15, 0, tzinfo=tz.utc), "da-empresa-a")
        self.mudanca(datetime(2027, 1, 18, 15, 0, tzinfo=tz.utc), "da-empresa-b",
                     imovel=self.imovel_b, usuario=self.corretor_b)
        self.assertEqual(self.observacoes(), ["da-empresa-a"])
        self.assertEqual(self.observacoes(self.outra.pk), ["da-empresa-b"])

    def test_ordem_mais_recente_primeiro_e_no_empate_o_maior_id(self):
        primeira = self.mudanca(datetime(2027, 1, 18, 15, 0, tzinfo=tz.utc), "empate-1")
        segunda = self.mudanca(datetime(2027, 1, 18, 15, 0, tzinfo=tz.utc), "empate-2")
        self.mudanca(datetime(2027, 1, 19, 15, 0, tzinfo=tz.utc), "mais-recente")
        self.mudanca(datetime(2027, 1, 15, 15, 0, tzinfo=tz.utc), "mais-antiga")
        self.assertGreater(segunda.pk, primeira.pk)
        self.assertEqual(self.observacoes(), ["mais-recente", "empate-2", "empate-1", "mais-antiga"])

    def test_inclui_as_mudancas_da_assinatura_de_contrato(self):
        relatorio = mudancas_de_situacao(self.empresa.pk)  # hoje de verdade: o setUp assinou hoje
        pares = {
            (historico.imovel.codigo, historico.situacao_anterior, historico.situacao_nova)
            for historico in relatorio["mudancas"]
        }
        self.assertIn(("VENDA-1", Imovel.Situacao.DISPONIVEL, Imovel.Situacao.VENDIDO), pares)
        self.assertIn(("ALUGA-1", Imovel.Situacao.DISPONIVEL, Imovel.Situacao.ALUGADO), pares)
        self.assertNotIn("OUTRA-1", {historico.imovel.codigo for historico in relatorio["mudancas"]})


class AreaDoGestorApiTests(RelatoriosBase, APITestCase):
    """Rotas da Área do gestor do app: só o gestor e só a empresa dele (critério 6, D-03)."""

    URLS_DO_APP = [
        "relatorio-acervo", "relatorio-contatos", "relatorio-acervo-por-corretor", "relatorio-mudancas-situacao",
    ]

    def test_ponta_a_ponta_com_login_real_e_token(self):
        fixar_taxa_alta_no_login(self)
        login = self.client.post(reverse("api-login"), {"email": self.gestor.email, "password": "senha-forte-123"})
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {login.data['token']}")
        response = self.client.get(reverse("relatorio-acervo-por-corretor"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data), {"linhas", "total"})
        self.assertEqual(response.data["total"], 4)
        self.assertTrue(response.data["linhas"])
        for linha in response.data["linhas"]:
            self.assertEqual(set(linha), {"corretor_id", "corretor", "ativo", "total"})

    def test_so_o_gestor_entra_nas_rotas_do_app(self):
        casos = [(self.corretor, 403), (self.admin, 403), (None, 401), (self.gestor, 200)]
        for usuario, esperado in casos:
            self.client.force_authenticate(usuario)
            for nome in self.URLS_DO_APP:
                with self.subTest(usuario=getattr(usuario, "email", None), url=nome):
                    self.assertEqual(self.client.get(reverse(nome)).status_code, esperado)

    def test_gestor_de_outra_empresa_ve_so_as_linhas_dela(self):
        self.client.force_authenticate(self.gestor_b)
        dados = self.client.get(reverse("relatorio-acervo-por-corretor")).data
        ids = {linha["corretor_id"] for linha in dados["linhas"]}
        self.assertEqual(ids, {self.corretor_b.pk})
        self.assertEqual(dados["total"], 2)

    def test_mudancas_sem_dias_usa_7_dias_decididos_no_servidor(self):
        self.client.force_authenticate(self.gestor)
        response = self.client.get(reverse("relatorio-mudancas-situacao"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        dados = response.json()
        self.assertEqual(set(dados), {"dias", "desde", "mudancas"})
        self.assertEqual(dados["dias"], 7)
        self.assertEqual(dados["desde"], (localdate() - timedelta(days=6)).isoformat())

    def test_mudancas_trazem_a_assinatura_de_contrato_e_nao_a_outra_empresa(self):
        self.client.force_authenticate(self.gestor)
        mudancas = self.client.get(reverse("relatorio-mudancas-situacao")).json()["mudancas"]
        por_codigo = {mudanca["imovel_codigo"]: mudanca for mudanca in mudancas}
        self.assertEqual(
            (por_codigo["VENDA-1"]["situacao_anterior"], por_codigo["VENDA-1"]["situacao_nova"]),
            ("DISPONIVEL", "VENDIDO"),
        )
        self.assertEqual(
            (por_codigo["ALUGA-1"]["situacao_anterior"], por_codigo["ALUGA-1"]["situacao_nova"]),
            ("DISPONIVEL", "ALUGADO"),
        )
        self.assertEqual(por_codigo["VENDA-1"]["alterado_por"], self.gestor.nome)
        self.assertNotIn("OUTRA-1", por_codigo)

    def test_cada_mudanca_tem_so_os_nove_campos_com_hora_de_sao_paulo(self):
        apto = Imovel.objects.get(empresa=self.empresa, codigo="APTO-1")
        apto.mudar_situacao(Imovel.Situacao.RESERVADO, self.gestor, observacao="Cliente vai pensar")
        self.client.force_authenticate(self.gestor)
        mudancas = self.client.get(reverse("relatorio-mudancas-situacao")).json()["mudancas"]
        self.assertTrue(mudancas)
        for mudanca in mudancas:
            self.assertEqual(
                set(mudanca),
                {"id", "imovel_id", "imovel_codigo", "imovel_titulo", "situacao_anterior", "situacao_nova",
                 "alterado_por", "alterado_em", "observacao"},
            )
            self.assertTrue(mudanca["alterado_em"].endswith("-03:00"), mudanca["alterado_em"])
        reserva = next(mudanca for mudanca in mudancas if mudanca["imovel_codigo"] == "APTO-1")
        self.assertEqual(reserva["imovel_id"], apto.pk)
        self.assertEqual(reserva["imovel_titulo"], apto.titulo)
        self.assertEqual(reserva["observacao"], "Cliente vai pensar")
        self.assertEqual(mudancas[0]["imovel_codigo"], "APTO-1")  # a mais recente vem primeiro

    def test_dias_invalido_da_400_com_a_chave_dias(self):
        self.client.force_authenticate(self.gestor)
        for valor in ("0", "366", "abc"):
            with self.subTest(dias=valor):
                response = self.client.get(reverse("relatorio-mudancas-situacao"), {"dias": valor})
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("dias", response.data)

    def test_dias_valido_vale_no_lugar_do_padrao(self):
        self.client.force_authenticate(self.gestor)
        response = self.client.get(reverse("relatorio-mudancas-situacao"), {"dias": "30"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["dias"], 30)

    def test_gestor_de_outra_empresa_ve_so_as_mudancas_dela(self):
        self.client.force_authenticate(self.gestor_b)
        mudancas = self.client.get(reverse("relatorio-mudancas-situacao")).json()["mudancas"]
        self.assertTrue(mudancas)  # a assinatura do contrato da empresa B
        self.assertEqual({mudanca["imovel_codigo"] for mudanca in mudancas}, {"OUTRA-1"})
