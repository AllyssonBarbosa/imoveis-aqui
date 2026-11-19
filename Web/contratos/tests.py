"""Testes da W12 — contrato e parcelas (critérios 1 e 3).

As contas de data e dinheiro usam datas e valores fixos, então o
resultado não depende do dia em que o teste roda.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from clientes.tests import OUTRO_CPF_VALIDO, criar_cliente
from contas.models import Usuario
from imoveis.models import HistoricoSituacao, Imovel
from imoveis.tests import (
    criar_corretor, criar_empresa, criar_gestor, criar_imovel, criar_proprietario, endereco_completo,
)

from .calculos import meses_de_vigencia, somar_meses, valores_das_parcelas, vencimentos
from .models import Contrato, Parcela
from .servicos import assinar_contrato, salvar_contrato

ONTEM = timezone.localdate() - timedelta(days=1)


# --- contas puras (sem banco) ------------------------------------------------


class ValoresDasParcelasTests(SimpleTestCase):
    def test_centavos_que_sobram_vao_na_ultima(self):
        self.assertEqual(
            valores_das_parcelas(Decimal("1000.00"), 3),
            [Decimal("333.33"), Decimal("333.33"), Decimal("333.34")],
        )

    def test_soma_sempre_fecha_com_o_total(self):
        for total, quantidade in [("1000.00", 3), ("99999.99", 7), ("250000.00", 360), ("0.10", 3)]:
            with self.subTest(total=total, quantidade=quantidade):
                valores = valores_das_parcelas(Decimal(total), quantidade)
                self.assertEqual(len(valores), quantidade)
                self.assertEqual(sum(valores), Decimal(total))
                self.assertTrue(all(isinstance(valor, Decimal) for valor in valores))
                self.assertTrue(all(valor == valor.quantize(Decimal("0.01")) for valor in valores))

    def test_divisao_exata_nao_mexe_na_ultima(self):
        self.assertEqual(valores_das_parcelas(Decimal("900.00"), 3), [Decimal("300.00")] * 3)

    def test_parcela_unica_e_o_total(self):
        self.assertEqual(valores_das_parcelas(Decimal("1234.56"), 1), [Decimal("1234.56")])

    def test_parcela_menor_que_um_centavo_e_recusada(self):
        with self.assertRaises(ValidationError):
            valores_das_parcelas(Decimal("0.02"), 3)


class DatasTests(SimpleTestCase):
    def test_dia_31_em_fevereiro_vira_ultimo_dia_e_volta_para_31(self):
        self.assertEqual(
            vencimentos(date(2027, 1, 10), 4, 31),
            [date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30), date(2027, 5, 31)],
        )

    def test_dia_29_em_ano_bissexto(self):
        self.assertEqual(somar_meses(date(2028, 1, 5), 1, 29), date(2028, 2, 29))
        self.assertEqual(somar_meses(date(2027, 1, 5), 1, 29), date(2027, 2, 28))

    def test_dia_30_em_fevereiro(self):
        self.assertEqual(somar_meses(date(2027, 1, 30), 1, 30), date(2027, 2, 28))

    def test_virada_do_ano(self):
        self.assertEqual(
            vencimentos(date(2027, 11, 20), 3, 10),
            [date(2027, 12, 10), date(2028, 1, 10), date(2028, 2, 10)],
        )

    def test_primeira_parcela_vence_no_mes_seguinte_ao_inicio(self):
        self.assertEqual(vencimentos(date(2027, 3, 1), 1, 5), [date(2027, 4, 5)])

    def test_vigencia_de_12_meses(self):
        self.assertEqual(meses_de_vigencia(date(2027, 1, 1), date(2027, 12, 31)), 12)

    def test_vigencia_de_30_meses_comecando_no_meio_do_mes(self):
        self.assertEqual(meses_de_vigencia(date(2027, 3, 15), date(2029, 9, 14)), 30)

    def test_vigencia_que_nao_fecha_em_meses_inteiros_e_recusada(self):
        with self.assertRaises(ValidationError) as erro:
            meses_de_vigencia(date(2027, 1, 1), date(2027, 12, 20))
        self.assertIn("meses inteiros", str(erro.exception))

    def test_fim_antes_do_inicio_e_recusado(self):
        with self.assertRaises(ValidationError):
            meses_de_vigencia(date(2027, 5, 1), date(2027, 4, 30))


# --- cenário com banco -------------------------------------------------------


class CenarioContrato:
    """Empresa A: gestor, dois corretores, um imóvel à venda, um para alugar e um cliente.
    Empresa B: gestor, corretor, imóvel e cliente. E o administrador."""

    def montar_cenario(self):
        self.empresa = criar_empresa()
        self.gestor = criar_gestor(self.empresa)
        self.corretor = criar_corretor(self.empresa)
        self.corretor2 = criar_corretor(self.empresa, email="corretor2@teste.com")
        proprietario = criar_proprietario(self.empresa)
        self.casa = criar_imovel(self.empresa, self.corretor, proprietario, codigo="VENDA-1")
        self.apto = criar_imovel(
            self.empresa, self.corretor, proprietario, codigo="ALUGA-1",
            finalidade=Imovel.Finalidade.ALUGUEL, preco_venda=None, preco_aluguel="2500.00",
        )
        self.cliente = criar_cliente(self.empresa)

        self.outra = criar_empresa(nome="Outra Imobiliária", cnpj="11.444.777/0001-61")
        self.gestor_b = criar_gestor(self.outra, email="gestor@outra.com")
        self.corretor_b = criar_corretor(self.outra, email="corretor@outra.com")
        self.imovel_b = criar_imovel(
            self.outra, self.corretor_b, criar_proprietario(self.outra), codigo="OUTRA-1"
        )
        self.cliente_b = criar_cliente(self.outra, documento=OUTRO_CPF_VALIDO)

        self.admin = Usuario.objects.create_superuser(
            email="admin@teste.com", senha="senha-forte-123", nome="Admin"
        )

    def novo_contrato(self, **extra):
        """Venda padrão: R$ 100.000 com R$ 10.000 de entrada em 3x, vencendo dia 31."""
        dados = dict(
            empresa=self.empresa, tipo=Contrato.Tipo.VENDA, imovel=self.casa, cliente=self.cliente,
            corretor=self.corretor, valor=Decimal("100000.00"), entrada=Decimal("10000.00"),
            numero_parcelas=3, dia_vencimento=31, data_inicio=date(2027, 1, 10),
        )
        dados.update(extra)
        return Contrato(**dados)

    def nova_locacao(self, **extra):
        """Locação padrão: R$ 2.500/mês de 01/01/2027 a 31/12/2027, vencendo dia 5."""
        dados = dict(
            tipo=Contrato.Tipo.LOCACAO, imovel=self.apto, valor=Decimal("2500.00"),
            entrada=Decimal("0"), numero_parcelas=None, dia_vencimento=5,
            data_inicio=date(2027, 1, 1), data_fim=date(2027, 12, 31),
        )
        dados.update(extra)
        return self.novo_contrato(**dados)

    def assinar_direto(self, contrato):
        """A assinatura de verdade é a etapa 3 — aqui só marca, para testar a trava."""
        Contrato.objects.filter(pk=contrato.pk).update(
            situacao=Contrato.Situacao.ASSINADO, data_assinatura=date(2027, 1, 10)
        )
        contrato.refresh_from_db()
        return contrato


class ContratoModelTests(CenarioContrato, TestCase):
    def setUp(self):
        self.montar_cenario()

    def erros(self, contrato):
        with self.assertRaises(ValidationError) as erro:
            contrato.full_clean()
        return erro.exception.message_dict

    def test_venda_valida(self):
        self.novo_contrato(criado_por=self.gestor).full_clean()

    def test_imovel_cliente_e_corretor_de_outra_empresa_sao_recusados(self):
        self.assertIn("imovel", self.erros(self.novo_contrato(imovel=self.imovel_b)))
        self.assertIn("cliente", self.erros(self.novo_contrato(cliente=self.cliente_b)))
        self.assertIn("corretor", self.erros(self.novo_contrato(corretor=self.corretor_b)))

    def test_corretor_precisa_ter_perfil_corretor(self):
        self.assertIn("corretor", self.erros(self.novo_contrato(corretor=self.gestor)))

    def test_venda_em_imovel_so_para_alugar_e_recusada(self):
        erros = self.erros(self.novo_contrato(imovel=self.apto))
        self.assertIn("não dá para fazer um contrato de venda", erros["imovel"][0])

    def test_locacao_em_imovel_so_a_venda_e_recusada(self):
        self.assertIn("imovel", self.erros(self.nova_locacao(imovel=self.casa)))

    def test_imovel_venda_e_aluguel_aceita_os_dois_tipos(self):
        self.casa.finalidade = Imovel.Finalidade.VENDA_E_ALUGUEL
        self.casa.preco_aluguel = Decimal("3000.00")
        self.casa.save()
        self.novo_contrato().full_clean()
        self.nova_locacao(imovel=self.casa).full_clean()

    def test_entrada_igual_ou_maior_que_o_valor_e_recusada(self):
        self.assertIn("entrada", self.erros(self.novo_contrato(entrada=Decimal("100000.00"))))

    def test_venda_sem_numero_de_parcelas_e_recusada(self):
        self.assertIn("numero_parcelas", self.erros(self.novo_contrato(numero_parcelas=None)))

    def test_venda_com_vigencia_e_recusada(self):
        self.assertIn("data_fim", self.erros(self.novo_contrato(data_fim=date(2027, 12, 31))))

    def test_dia_de_vencimento_fora_de_1_a_31_e_recusado(self):
        for dia in (0, 32):
            with self.subTest(dia=dia):
                self.assertIn("dia_vencimento", self.erros(self.novo_contrato(dia_vencimento=dia)))

    def test_locacao_sem_vigencia_e_recusada(self):
        self.assertIn("data_fim", self.erros(self.nova_locacao(data_fim=None)))

    def test_locacao_com_entrada_e_recusada(self):
        self.assertIn("entrada", self.erros(self.nova_locacao(entrada=Decimal("5000.00"))))

    def test_locacao_calcula_o_numero_de_parcelas_pela_vigencia(self):
        contrato = self.nova_locacao(numero_parcelas=99)  # o digitado é ignorado
        contrato.full_clean()
        self.assertEqual(contrato.numero_parcelas, 12)


class GerarParcelasTests(CenarioContrato, TestCase):
    """Critério 3: ao salvar, o contrato gera todas as parcelas com número, vencimento e valor."""

    def setUp(self):
        self.montar_cenario()

    def parcelas(self, contrato):
        return list(contrato.parcelas.values_list("numero", "vencimento", "valor"))

    def test_venda_gera_todas_as_parcelas(self):
        contrato = salvar_contrato(self.novo_contrato(), self.gestor)
        self.assertEqual(self.parcelas(contrato), [
            (1, date(2027, 2, 28), Decimal("30000.00")),
            (2, date(2027, 3, 31), Decimal("30000.00")),
            (3, date(2027, 4, 30), Decimal("30000.00")),
        ])
        self.assertEqual(contrato.criado_por, self.gestor)

    def test_soma_das_parcelas_fecha_com_valor_menos_entrada(self):
        contrato = salvar_contrato(
            self.novo_contrato(valor=Decimal("100000.00"), entrada=Decimal("0.01"), numero_parcelas=7),
            self.gestor,
        )
        valores = [valor for _, _, valor in self.parcelas(contrato)]
        self.assertEqual(sum(valores), Decimal("99999.99"))
        self.assertEqual(valores[:6], [Decimal("14285.71")] * 6)
        self.assertEqual(valores[6], Decimal("14285.73"))

    def test_parcelas_pertencem_a_empresa_do_contrato(self):
        contrato = salvar_contrato(self.novo_contrato(), self.gestor)
        self.assertEqual(set(contrato.parcelas.values_list("empresa_id", flat=True)), {self.empresa.pk})

    def test_locacao_gera_um_aluguel_por_mes_da_vigencia(self):
        contrato = salvar_contrato(self.nova_locacao(), self.gestor)
        parcelas = self.parcelas(contrato)
        self.assertEqual(len(parcelas), 12)
        self.assertTrue(all(valor == Decimal("2500.00") for _, _, valor in parcelas))
        self.assertEqual(parcelas[0][1], date(2027, 2, 5))
        self.assertEqual(parcelas[-1][1], date(2028, 1, 5))
        self.assertEqual(contrato.total_parcelado, Decimal("30000.00"))

    def test_editar_valor_do_rascunho_gera_as_parcelas_de_novo(self):
        contrato = salvar_contrato(self.novo_contrato(), self.gestor)
        contrato.numero_parcelas = 2
        contrato.dia_vencimento = 15
        salvar_contrato(contrato, self.gestor)
        self.assertEqual(self.parcelas(contrato), [
            (1, date(2027, 2, 15), Decimal("45000.00")),
            (2, date(2027, 3, 15), Decimal("45000.00")),
        ])

    def test_editar_so_observacao_mantem_as_mesmas_parcelas(self):
        contrato = salvar_contrato(self.novo_contrato(), self.gestor)
        ids_antes = list(contrato.parcelas.values_list("id", flat=True))
        contrato.observacoes = "Cliente pediu boleto por e-mail."
        salvar_contrato(contrato, self.gestor)
        self.assertEqual(list(contrato.parcelas.values_list("id", flat=True)), ids_antes)

    def test_contrato_assinado_nao_aceita_mudar_valores(self):
        contrato = self.assinar_direto(salvar_contrato(self.novo_contrato(), self.gestor))
        parcelas_antes = self.parcelas(contrato)
        for campo, valor in [
            ("valor", Decimal("120000.00")), ("numero_parcelas", 10), ("dia_vencimento", 1),
            ("data_inicio", date(2027, 6, 1)), ("cliente", None), ("corretor", None),
        ]:
            with self.subTest(campo=campo):
                contrato.refresh_from_db()
                if campo == "cliente":
                    valor = criar_cliente(self.empresa, documento="935.411.347-80", nome="Outro")
                if campo == "corretor":
                    valor = self.corretor2
                setattr(contrato, campo, valor)
                with self.assertRaises(ValidationError) as erro:
                    salvar_contrato(contrato, self.gestor)
                self.assertIn("Contrato assinado", str(erro.exception))
        self.assertEqual(self.parcelas(contrato), parcelas_antes)

    def test_contrato_assinado_aceita_mudar_observacao(self):
        contrato = self.assinar_direto(salvar_contrato(self.novo_contrato(), self.gestor))
        contrato.observacoes = "Reconhecimento de firma feito."
        salvar_contrato(contrato, self.gestor)
        contrato.refresh_from_db()
        self.assertEqual(contrato.observacoes, "Reconhecimento de firma feito.")

    def test_erro_de_validacao_nao_grava_nada(self):
        with self.assertRaises(ValidationError):
            salvar_contrato(self.novo_contrato(entrada=Decimal("200000.00")), self.gestor)
        self.assertFalse(Contrato.objects.exists())
        self.assertFalse(Parcela.objects.exists())


def dados_formulario(contrato_base, **extra):
    dados = {
        "tipo": "VENDA", "imovel": contrato_base.casa.pk, "cliente": contrato_base.cliente.pk,
        "corretor": contrato_base.corretor.pk, "valor": "100000.00", "entrada": "10000.00",
        "numero_parcelas": "3", "dia_vencimento": "31", "data_inicio": "10/01/2027",
        "data_fim": "", "observacoes": "",
    }
    dados.update(extra)
    return dados


class PainelContratoTests(CenarioContrato, TestCase):
    def setUp(self):
        self.montar_cenario()

    def criar_pelo_painel(self, **extra):
        self.client.force_login(self.gestor)
        return self.client.post(reverse("contratos:contrato_create"), dados_formulario(self, **extra))

    def test_gestor_cadastra_contrato_e_parcelas_sao_geradas(self):
        response = self.criar_pelo_painel()
        contrato = Contrato.objects.get()
        self.assertRedirects(response, reverse("contratos:contrato_detail", args=[contrato.pk]))
        self.assertEqual(contrato.empresa, self.empresa)
        self.assertEqual(contrato.situacao, Contrato.Situacao.RASCUNHO)
        self.assertEqual(contrato.parcelas.count(), 3)

        detalhe = self.client.get(reverse("contratos:contrato_detail", args=[contrato.pk]))
        self.assertContains(detalhe, "28/02/2027")
        self.assertContains(detalhe, "30.000,00")

    def test_locacao_pelo_painel_sem_entrada_e_sem_numero_de_parcelas(self):
        self.criar_pelo_painel(
            tipo="LOCACAO", imovel=self.apto.pk, valor="2500.00", entrada="", numero_parcelas="",
            dia_vencimento="5", data_inicio="01/01/2027", data_fim="31/12/2027",
        )
        contrato = Contrato.objects.get()
        self.assertEqual(contrato.numero_parcelas, 12)
        self.assertEqual(contrato.entrada, 0)
        self.assertEqual(contrato.parcelas.count(), 12)

    def test_formulario_mostra_erro_de_validacao(self):
        response = self.criar_pelo_painel(entrada="100000.00")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A entrada precisa ser menor que o valor total.")
        self.assertFalse(Contrato.objects.exists())

    def test_data_no_formato_errado_mostra_erro(self):
        response = self.criar_pelo_painel(data_inicio="2027-01-10")
        self.assertContains(response, "Informe a data no formato dd/mm/aaaa")

    def test_imovel_de_outra_empresa_nao_e_aceito(self):
        response = self.criar_pelo_painel(imovel=self.imovel_b.pk)
        self.assertEqual(response.status_code, 200)
        self.assertIn("imovel", response.context["form"].errors)
        self.assertFalse(Contrato.objects.exists())

    def test_listas_do_formulario_so_trazem_dados_da_empresa(self):
        self.client.force_login(self.gestor)
        form = self.client.get(reverse("contratos:contrato_create")).context["form"]
        self.assertNotIn(self.imovel_b, form.fields["imovel"].queryset)
        self.assertNotIn(self.cliente_b, form.fields["cliente"].queryset)
        self.assertNotIn(self.corretor_b, form.fields["corretor"].queryset)
        self.assertNotIn(self.gestor, form.fields["corretor"].queryset)

    def test_corretor_e_administrador_nao_cadastram(self):
        for usuario in (self.corretor, self.admin):
            with self.subTest(perfil=usuario.perfil):
                self.client.force_login(usuario)
                response = self.client.post(reverse("contratos:contrato_create"), dados_formulario(self))
                self.assertEqual(response.status_code, 403)
        self.assertFalse(Contrato.objects.exists())

    def test_corretor_ve_so_os_contratos_dele(self):
        meu = salvar_contrato(self.novo_contrato(), self.gestor)
        do_outro = salvar_contrato(self.novo_contrato(corretor=self.corretor2), self.gestor)
        self.client.force_login(self.corretor)
        lista = self.client.get(reverse("contratos:contrato_list"))
        self.assertEqual(list(lista.context["contratos"]), [meu])
        self.assertEqual(
            self.client.get(reverse("contratos:contrato_detail", args=[meu.pk])).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse("contratos:contrato_detail", args=[do_outro.pk])).status_code, 404
        )

    def test_corretor_nao_edita(self):
        contrato = salvar_contrato(self.novo_contrato(), self.gestor)
        self.client.force_login(self.corretor)
        response = self.client.get(reverse("contratos:contrato_update", args=[contrato.pk]))
        self.assertEqual(response.status_code, 403)

    def test_gestor_ve_todos_da_empresa_e_nada_da_outra(self):
        meu = salvar_contrato(self.novo_contrato(), self.gestor)
        de_fora = salvar_contrato(
            self.novo_contrato(
                empresa=self.outra, imovel=self.imovel_b, cliente=self.cliente_b, corretor=self.corretor_b
            ),
            self.gestor_b,
        )
        self.client.force_login(self.gestor)
        self.assertEqual(list(self.client.get(reverse("contratos:contrato_list")).context["contratos"]), [meu])
        for nome in ("contratos:contrato_detail", "contratos:contrato_update"):
            with self.subTest(rota=nome):
                self.assertEqual(self.client.get(reverse(nome, args=[de_fora.pk])).status_code, 404)

    def test_administrador_nao_ve_a_lista(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("contratos:contrato_list")).status_code, 403)

    def test_filtro_por_tipo(self):
        venda = salvar_contrato(self.novo_contrato(), self.gestor)
        salvar_contrato(self.nova_locacao(), self.gestor)
        self.client.force_login(self.gestor)
        response = self.client.get(reverse("contratos:contrato_list"), {"tipo": "VENDA"})
        self.assertEqual(list(response.context["contratos"]), [venda])

    def test_edicao_do_rascunho_gera_parcelas_de_novo(self):
        contrato = salvar_contrato(self.novo_contrato(), self.gestor)
        self.client.force_login(self.gestor)
        self.client.post(
            reverse("contratos:contrato_update", args=[contrato.pk]),
            dados_formulario(self, numero_parcelas="6"),
        )
        self.assertEqual(contrato.parcelas.count(), 6)

    def test_contrato_assinado_ignora_valores_enviados_e_salva_observacao(self):
        contrato = self.assinar_direto(salvar_contrato(self.novo_contrato(), self.gestor))
        self.client.force_login(self.gestor)
        tela = self.client.get(reverse("contratos:contrato_update", args=[contrato.pk]))
        self.assertTrue(tela.context["form"].fields["valor"].disabled)
        self.assertFalse(tela.context["form"].fields["observacoes"].disabled)

        self.client.post(
            reverse("contratos:contrato_update", args=[contrato.pk]),
            dados_formulario(self, valor="1.00", numero_parcelas="1", observacoes="Firma reconhecida"),
        )
        contrato.refresh_from_db()
        self.assertEqual(contrato.valor, Decimal("100000.00"))
        self.assertEqual(contrato.parcelas.count(), 3)
        self.assertEqual(contrato.observacoes, "Firma reconhecida")


class ApiContratoTests(CenarioContrato, APITestCase):
    def setUp(self):
        self.montar_cenario()

    def dados(self, **extra):
        dados = {
            "tipo": "VENDA", "imovel": self.casa.pk, "cliente": self.cliente.pk,
            "corretor": self.corretor.pk, "valor": "100000.00", "entrada": "10000.00",
            "numero_parcelas": 3, "dia_vencimento": 31, "data_inicio": "2027-01-10",
        }
        dados.update(extra)
        return dados

    def test_gestor_cria_contrato_e_recebe_as_parcelas(self):
        self.client.force_authenticate(self.gestor)
        response = self.client.post(reverse("contrato-list"), self.dados(), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["situacao"], "RASCUNHO")
        self.assertEqual(
            [(p["numero"], p["vencimento"], p["valor"]) for p in response.data["parcelas"]],
            [(1, "2027-02-28", "30000.00"), (2, "2027-03-31", "30000.00"), (3, "2027-04-30", "30000.00")],
        )
        self.assertEqual(Contrato.objects.get().empresa, self.empresa)

    def test_parcelas_enviadas_pelo_app_sao_ignoradas(self):
        self.client.force_authenticate(self.gestor)
        response = self.client.post(
            reverse("contrato-list"),
            self.dados(parcelas=[{"numero": 1, "vencimento": "2027-01-01", "valor": "1.00"}]),
            format="json",
        )
        self.assertEqual(len(response.data["parcelas"]), 3)

    def test_locacao_pela_api(self):
        self.client.force_authenticate(self.gestor)
        response = self.client.post(reverse("contrato-list"), self.dados(
            tipo="LOCACAO", imovel=self.apto.pk, valor="2500.00", entrada=None, numero_parcelas=None,
            dia_vencimento=5, data_inicio="2027-01-01", data_fim="2027-12-31",
        ), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["numero_parcelas"], 12)

    def test_corretor_nao_cria_e_administrador_nao_acessa(self):
        self.client.force_authenticate(self.corretor)
        response = self.client.post(reverse("contrato-list"), self.dados(), format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(reverse("contrato-list")).status_code, status.HTTP_403_FORBIDDEN)

    def test_anonimo_recebe_401(self):
        self.assertEqual(self.client.get(reverse("contrato-list")).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_corretor_lista_so_os_dele(self):
        meu = salvar_contrato(self.novo_contrato(), self.gestor)
        salvar_contrato(self.novo_contrato(corretor=self.corretor2), self.gestor)
        self.client.force_authenticate(self.corretor)
        response = self.client.get(reverse("contrato-list"))
        self.assertEqual([item["id"] for item in response.data], [meu.pk])

    def test_contrato_de_outra_empresa_da_404(self):
        de_fora = salvar_contrato(
            self.novo_contrato(
                empresa=self.outra, imovel=self.imovel_b, cliente=self.cliente_b, corretor=self.corretor_b
            ),
            self.gestor_b,
        )
        self.client.force_authenticate(self.gestor)
        url = reverse("contrato-detail", args=[de_fora.pk])
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            self.client.patch(url, {"observacoes": "x"}, format="json").status_code, status.HTTP_404_NOT_FOUND
        )

    def test_imovel_de_outra_empresa_e_recusado(self):
        self.client.force_authenticate(self.gestor)
        response = self.client.post(reverse("contrato-list"), self.dados(imovel=self.imovel_b.pk), format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("imovel", response.data)

    def test_patch_no_rascunho_gera_parcelas_de_novo(self):
        contrato = salvar_contrato(self.novo_contrato(), self.gestor)
        self.client.force_authenticate(self.gestor)
        response = self.client.patch(
            reverse("contrato-detail", args=[contrato.pk]), {"numero_parcelas": 2}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual([p["valor"] for p in response.data["parcelas"]], ["45000.00", "45000.00"])

    def test_patch_de_valor_em_contrato_assinado_e_recusado(self):
        contrato = self.assinar_direto(salvar_contrato(self.novo_contrato(), self.gestor))
        self.client.force_authenticate(self.gestor)
        response = self.client.patch(
            reverse("contrato-detail", args=[contrato.pk]), {"valor": "1.00"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Contrato assinado", str(response.data))

    def test_situacao_nao_e_alterada_pela_api(self):
        contrato = salvar_contrato(self.novo_contrato(), self.gestor)
        self.client.force_authenticate(self.gestor)
        self.client.patch(reverse("contrato-detail", args=[contrato.pk]), {"situacao": "ASSINADO"}, format="json")
        contrato.refresh_from_db()
        self.assertEqual(contrato.situacao, Contrato.Situacao.RASCUNHO)

    def test_nao_permite_excluir(self):
        contrato = salvar_contrato(self.novo_contrato(), self.gestor)
        self.client.force_authenticate(self.gestor)
        response = self.client.delete(reverse("contrato-detail", args=[contrato.pk]))
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class SeedDemoTests(TestCase):
    def test_seed_cria_cliente_e_contrato_com_parcelas_sem_duplicar(self):
        import shutil
        import tempfile
        from io import StringIO
        from pathlib import Path

        from django.core.management import call_command
        from django.test import override_settings

        from clientes.models import Cliente

        from .models import Baixa

        pasta = tempfile.mkdtemp()  # os comprovantes do seed não vão para a pasta de verdade
        self.addCleanup(shutil.rmtree, pasta, ignore_errors=True)
        with override_settings(ARQUIVOS_PRIVADOS_ROOT=Path(pasta)):
            for _ in range(2):
                call_command("seed_demo", stdout=StringIO())
        contrato = Contrato.objects.get()
        self.assertEqual(Baixa.objects.count(), 2)
        primeira, segunda = contrato.parcelas.com_pagamentos()[:2]
        self.assertTrue(primeira.paga)
        self.assertFalse(segunda.paga)
        self.assertGreater(segunda.total_pago, 0)
        # a 2ª venceu há uns 2 meses e foi paga pela metade: aparece como atrasada (etapa 6)
        self.assertEqual(contrato.parcelas.com_situacao().get(numero=2).situacao, "ATRASADA")
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(contrato.cliente.nome_razao_social, "João Comprador")
        self.assertEqual(contrato.parcelas.count(), 60)
        self.assertEqual(sum(contrato.parcelas.values_list("valor", flat=True)), Decimal("300000.00"))
        self.assertTrue(contrato.assinado)
        self.assertEqual(contrato.imovel.situacao, Imovel.Situacao.VENDIDO)


# --- etapa 3: assinatura (critério 1) -----------------------------------------


class AssinaturaTests(CenarioContrato, TestCase):
    def setUp(self):
        self.montar_cenario()

    def test_assinar_venda_marca_situacao_data_e_quem_assinou(self):
        contrato = assinar_contrato(salvar_contrato(self.novo_contrato(), self.gestor), self.gestor, ONTEM)
        contrato.refresh_from_db()
        self.assertEqual(contrato.situacao, Contrato.Situacao.ASSINADO)
        self.assertEqual(contrato.data_assinatura, ONTEM)
        self.assertEqual(contrato.assinado_por, self.gestor)

    def test_venda_assinada_deixa_o_imovel_vendido_com_historico(self):
        contrato = assinar_contrato(salvar_contrato(self.novo_contrato(), self.gestor), self.gestor, ONTEM)
        self.casa.refresh_from_db()
        self.assertEqual(self.casa.situacao, Imovel.Situacao.VENDIDO)
        mudanca = HistoricoSituacao.objects.get(imovel=self.casa)
        self.assertEqual(mudanca.situacao_anterior, Imovel.Situacao.DISPONIVEL)
        self.assertEqual(mudanca.situacao_nova, Imovel.Situacao.VENDIDO)
        self.assertEqual(mudanca.alterado_por, self.gestor)
        self.assertIn(f"Contrato #{contrato.pk}", mudanca.observacao)

    def test_locacao_assinada_deixa_o_imovel_alugado(self):
        assinar_contrato(salvar_contrato(self.nova_locacao(), self.gestor), self.gestor, ONTEM)
        self.apto.refresh_from_db()
        self.assertEqual(self.apto.situacao, Imovel.Situacao.ALUGADO)

    def test_imovel_reservado_pode_ser_assinado(self):
        self.casa.mudar_situacao(Imovel.Situacao.RESERVADO, self.gestor, "Sinal pago")
        assinar_contrato(salvar_contrato(self.novo_contrato(), self.gestor), self.gestor, ONTEM)
        self.casa.refresh_from_db()
        self.assertEqual(self.casa.situacao, Imovel.Situacao.VENDIDO)

    def test_pode_assinar_com_data_de_hoje_mas_nao_no_futuro(self):
        contrato = salvar_contrato(self.novo_contrato(), self.gestor)
        with self.assertRaises(ValidationError) as erro:
            assinar_contrato(contrato, self.gestor, timezone.localdate() + timedelta(days=1))
        self.assertIn("data_assinatura", erro.exception.message_dict)
        assinar_contrato(contrato, self.gestor, timezone.localdate())

    def test_contrato_ja_assinado_nao_assina_de_novo(self):
        contrato = assinar_contrato(salvar_contrato(self.novo_contrato(), self.gestor), self.gestor, ONTEM)
        with self.assertRaises(ValidationError) as erro:
            assinar_contrato(contrato, self.gestor, ONTEM)
        self.assertIn("já está assinado", str(erro.exception))

    def test_imovel_nao_tem_dois_contratos_assinados(self):
        primeiro = salvar_contrato(self.novo_contrato(), self.gestor)
        segundo = salvar_contrato(self.novo_contrato(observacoes="proposta concorrente"), self.gestor)
        assinar_contrato(primeiro, self.gestor, ONTEM)
        with self.assertRaises(ValidationError) as erro:
            assinar_contrato(segundo, self.gestor, ONTEM)
        self.assertIn(f"já tem um contrato assinado (#{primeiro.pk})", str(erro.exception))
        segundo.refresh_from_db()
        self.assertEqual(segundo.situacao, Contrato.Situacao.RASCUNHO)
        self.assertEqual(HistoricoSituacao.objects.filter(imovel=self.casa).count(), 1)

    def test_banco_tambem_recusa_dois_contratos_assinados(self):
        primeiro = salvar_contrato(self.novo_contrato(), self.gestor)
        segundo = salvar_contrato(self.novo_contrato(), self.gestor)
        Contrato.objects.filter(pk=primeiro.pk).update(situacao=Contrato.Situacao.ASSINADO)
        with self.assertRaises(IntegrityError):
            Contrato.objects.filter(pk=segundo.pk).update(situacao=Contrato.Situacao.ASSINADO)

    def test_imovel_ja_vendido_sem_contrato_nao_aceita_assinatura(self):
        self.casa.mudar_situacao(Imovel.Situacao.VENDIDO, self.gestor, "Vendido por fora")
        with self.assertRaises(ValidationError) as erro:
            assinar_contrato(salvar_contrato(self.novo_contrato(), self.gestor), self.gestor, ONTEM)
        self.assertIn("só imóvel disponível ou reservado", str(erro.exception))

    def test_finalidade_mudada_depois_do_rascunho_e_conferida_na_assinatura(self):
        contrato = salvar_contrato(self.novo_contrato(), self.gestor)
        Imovel.objects.filter(pk=self.casa.pk).update(
            finalidade=Imovel.Finalidade.ALUGUEL, preco_aluguel=Decimal("3000.00")
        )
        with self.assertRaises(ValidationError):
            assinar_contrato(contrato, self.gestor, ONTEM)

    def test_se_algo_falha_no_meio_nada_fica_gravado(self):
        """Transação: se a mudança do imóvel falhar, o contrato não fica assinado."""
        contrato = salvar_contrato(self.novo_contrato(), self.gestor)
        with mock.patch.object(Imovel, "mudar_situacao", side_effect=RuntimeError("falha simulada")):
            with self.assertRaises(RuntimeError):
                assinar_contrato(contrato, self.gestor, ONTEM)
        contrato.refresh_from_db()
        self.assertEqual(contrato.situacao, Contrato.Situacao.RASCUNHO)
        self.assertIsNone(contrato.data_assinatura)


class VitrineAposAssinaturaTests(CenarioContrato, APITestCase):
    """O imóvel assinado sai da vitrine; o cliente nunca aparece nela (antes ou depois)."""

    def setUp(self):
        self.montar_cenario()
        self.casa.endereco = endereco_completo()
        self.casa.publicado = True
        self.casa.save()
        self.cidade = self.casa.endereco.cidade
        # outro imóvel da mesma empresa continua na vitrine (perfis e busca seguem com conteúdo)
        self.outra_casa = criar_imovel(
            self.empresa, self.corretor, self.casa.proprietario, codigo="VENDA-2",
            titulo="Outra casa à venda", endereco=endereco_completo(), publicado=True,
        )
        self.contrato = salvar_contrato(self.novo_contrato(), self.gestor)

    def ids_na_busca(self):
        response = self.client.get(reverse("publico-vitrine-cidade", args=[self.cidade.pk]))
        return {item["id"] for item in response.data["results"]}

    def paginas_publicas(self, imovel):
        urls = [
            reverse("site_publico:inicio"),
            reverse("site_publico:vitrine", args=[self.cidade.pk]),
            reverse("site_publico:empresa", args=[self.empresa.pk]),
            reverse("site_publico:corretor", args=[self.corretor.pk]),
            reverse("publico-vitrine-cidade", args=[self.cidade.pk]),
            reverse("site_publico:imovel", args=[imovel.pk]),
            reverse("publico-imovel", args=[imovel.pk]),
        ]
        return [self.client.get(url) for url in urls]

    def test_antes_de_assinar_o_imovel_esta_na_vitrine(self):
        self.assertIn(self.casa.pk, self.ids_na_busca())
        self.assertEqual(self.client.get(reverse("site_publico:imovel", args=[self.casa.pk])).status_code, 200)

    def test_depois_de_assinar_o_imovel_sai_do_site_e_da_api(self):
        assinar_contrato(self.contrato, self.gestor, ONTEM)
        self.assertNotIn(self.casa.pk, self.ids_na_busca())
        self.assertIn(self.outra_casa.pk, self.ids_na_busca())
        for url in (
            reverse("site_publico:imovel", args=[self.casa.pk]),
            reverse("publico-imovel", args=[self.casa.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_depois_de_assinar_o_imovel_nao_recebe_contato(self):
        assinar_contrato(self.contrato, self.gestor, ONTEM)
        response = self.client.post(
            reverse("publico-imovel-contato", args=[self.casa.pk]), {"canal": "WHATSAPP"}, format="json"
        )
        self.assertEqual(response.status_code, 404)

    def test_cliente_nunca_aparece_na_vitrine(self):
        segredos = [
            self.cliente.nome_razao_social, self.cliente.documento, self.cliente.documento_formatado,
            self.cliente.telefone,
        ]
        antes = self.paginas_publicas(self.casa)
        assinar_contrato(self.contrato, self.gestor, ONTEM)
        depois = self.paginas_publicas(self.outra_casa)
        for response in antes + depois:
            self.assertEqual(response.status_code, 200, response.request["PATH_INFO"])
            corpo = response.content.decode()
            for segredo in segredos:
                self.assertNotIn(segredo, corpo, f"{response.request['PATH_INFO']} vazou {segredo}")


class PainelAssinaturaTests(CenarioContrato, TestCase):
    def setUp(self):
        self.montar_cenario()
        self.contrato = salvar_contrato(self.novo_contrato(), self.gestor)

    def assinar(self, usuario, contrato=None, data=None):
        self.client.force_login(usuario)
        contrato = contrato or self.contrato
        return self.client.post(
            reverse("contratos:contrato_assinar", args=[contrato.pk]),
            {"data_assinatura": data or ONTEM.strftime("%d/%m/%Y")},
        )

    def test_gestor_assina_pelo_painel(self):
        response = self.assinar(self.gestor)
        # fetch_redirect_response=False: senão o assertRedirects abre a página e "consome" a mensagem
        self.assertRedirects(
            response, reverse("contratos:contrato_detail", args=[self.contrato.pk]),
            fetch_redirect_response=False,
        )
        self.contrato.refresh_from_db()
        self.casa.refresh_from_db()
        self.assertTrue(self.contrato.assinado)
        self.assertEqual(self.casa.situacao, Imovel.Situacao.VENDIDO)
        detalhe = self.client.get(response["Location"])
        self.assertContains(detalhe, "saiu da vitrine")
        self.assertNotContains(detalhe, "Assinar contrato")

    def test_tela_mostra_o_botao_so_para_o_gestor_no_rascunho(self):
        url = reverse("contratos:contrato_detail", args=[self.contrato.pk])
        self.client.force_login(self.gestor)
        self.assertContains(self.client.get(url), "Assinar contrato")
        self.client.force_login(self.corretor)
        self.assertNotContains(self.client.get(url), "Assinar contrato")

    def test_corretor_e_administrador_nao_assinam(self):
        for usuario in (self.corretor, self.admin):
            with self.subTest(perfil=usuario.perfil):
                self.assertEqual(self.assinar(usuario).status_code, 403)
        self.contrato.refresh_from_db()
        self.assertFalse(self.contrato.assinado)

    def test_contrato_de_outra_empresa_da_404(self):
        self.assertEqual(self.assinar(self.gestor_b).status_code, 404)

    def test_segundo_contrato_do_imovel_mostra_mensagem(self):
        segundo = salvar_contrato(self.novo_contrato(), self.gestor)
        self.assinar(self.gestor)
        response = self.assinar(self.gestor, contrato=segundo)
        detalhe = self.client.get(response["Location"])
        self.assertContains(detalhe, "já tem um contrato assinado")
        segundo.refresh_from_db()
        self.assertFalse(segundo.assinado)

    def test_data_no_formato_errado_nao_assina(self):
        self.assinar(self.gestor, data="2027-01-10")
        self.contrato.refresh_from_db()
        self.assertFalse(self.contrato.assinado)

    def test_assinar_so_aceita_post(self):
        self.client.force_login(self.gestor)
        response = self.client.get(reverse("contratos:contrato_assinar", args=[self.contrato.pk]))
        self.assertEqual(response.status_code, 405)


class ApiAssinaturaTests(CenarioContrato, APITestCase):
    def setUp(self):
        self.montar_cenario()
        self.contrato = salvar_contrato(self.novo_contrato(), self.gestor)

    def assinar(self, usuario, contrato=None, **dados):
        self.client.force_authenticate(usuario)
        contrato = contrato or self.contrato
        return self.client.post(
            reverse("contrato-assinar", args=[contrato.pk]),
            dados or {"data_assinatura": ONTEM.isoformat()}, format="json",
        )

    def test_gestor_assina_pela_api(self):
        response = self.assinar(self.gestor)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["situacao"], "ASSINADO")
        self.assertEqual(response.data["data_assinatura"], ONTEM.isoformat())
        self.casa.refresh_from_db()
        self.assertEqual(self.casa.situacao, Imovel.Situacao.VENDIDO)

    def test_corretor_nao_assina(self):
        self.assertEqual(self.assinar(self.corretor).status_code, status.HTTP_403_FORBIDDEN)

    def test_contrato_de_outra_empresa_da_404(self):
        self.assertEqual(self.assinar(self.gestor_b).status_code, status.HTTP_404_NOT_FOUND)

    def test_sem_data_e_recusado(self):
        response = self.assinar(self.gestor, data_assinatura="")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_segundo_contrato_do_imovel_e_recusado(self):
        segundo = salvar_contrato(self.novo_contrato(), self.gestor)
        self.assinar(self.gestor)
        response = self.assinar(self.gestor, contrato=segundo)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("já tem um contrato assinado", str(response.data))


class TravaDaSituacaoManualTests(CenarioContrato, APITestCase):
    """Com contrato assinado, a situação do imóvel não muda à mão (altera o mudar_situacao da W08)."""

    def setUp(self):
        self.montar_cenario()
        self.contrato = salvar_contrato(self.novo_contrato(), self.gestor)

    def test_com_contrato_assinado_a_situacao_nao_muda_a_mao(self):
        assinar_contrato(self.contrato, self.gestor, ONTEM)
        self.casa.refresh_from_db()
        with self.assertRaises(ValidationError) as erro:
            self.casa.mudar_situacao(Imovel.Situacao.DISPONIVEL, self.gestor, "voltar pra vitrine")
        self.assertIn(f"contrato assinado (#{self.contrato.pk})", str(erro.exception))
        self.casa.refresh_from_db()
        self.assertEqual(self.casa.situacao, Imovel.Situacao.VENDIDO)
        self.assertEqual(HistoricoSituacao.objects.filter(imovel=self.casa).count(), 1)

    def test_contrato_em_rascunho_nao_trava(self):
        self.casa.mudar_situacao(Imovel.Situacao.RESERVADO, self.gestor, "Sinal pago")
        self.casa.refresh_from_db()
        self.assertEqual(self.casa.situacao, Imovel.Situacao.RESERVADO)

    def test_tela_da_w08_mostra_o_motivo(self):
        assinar_contrato(self.contrato, self.gestor, ONTEM)
        self.client.force_login(self.gestor)
        response = self.client.post(
            reverse("imoveis:gestao_imovel_situacao", args=[self.casa.pk]),
            {"situacao": "DISPONIVEL", "observacao": ""},
        )
        self.assertContains(response, "não pode ser mudada à mão")
        self.casa.refresh_from_db()
        self.assertEqual(self.casa.situacao, Imovel.Situacao.VENDIDO)

    def test_api_da_w08_recusa(self):
        assinar_contrato(self.contrato, self.gestor, ONTEM)
        self.client.force_authenticate(self.gestor)
        response = self.client.post(
            reverse("gestao-imovel-situacao", args=[self.casa.pk]), {"situacao": "DISPONIVEL"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("não pode ser mudada à mão", str(response.data))
