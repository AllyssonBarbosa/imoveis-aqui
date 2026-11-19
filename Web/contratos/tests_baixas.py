"""Testes da W12 — etapa 5: baixa de parcela com comprovante (critério 4).

- várias baixas por parcela; pagamento parcial e a mais são aceitos;
- a parcela está paga quando a soma das baixas cobre o valor;
- só o gestor registra; o corretor do contrato vê e baixa o comprovante;
- o comprovante fica na pasta privada (sem URL), como os documentos da etapa 4.
"""

import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Baixa, Parcela
from .servicos import assinar_contrato, registrar_baixa, salvar_contrato
from .tests import ONTEM, CenarioContrato
from .tests_arquivos import CONTEUDO_PDF, gerar_pdf, gerar_png

HOJE = timezone.localdate()


class BaixaBase(CenarioContrato):
    """Venda assinada de R$ 90.000 parcelados em 3x de R$ 30.000 (empresa A, corretor 1),
    uma locação assinada do corretor 2 e uma venda assinada da empresa B."""

    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        configuracao = override_settings(ARQUIVOS_PRIVADOS_ROOT=Path(self.pasta))
        configuracao.enable()
        self.addCleanup(configuracao.disable)
        self.addCleanup(shutil.rmtree, self.pasta, ignore_errors=True)

        self.montar_cenario()
        self.contrato = assinar_contrato(salvar_contrato(self.novo_contrato(), self.gestor), self.gestor, ONTEM)
        self.parcela = self.contrato.parcelas.get(numero=1)

        self.locacao_do_outro = assinar_contrato(
            salvar_contrato(self.nova_locacao(corretor=self.corretor2), self.gestor), self.gestor, ONTEM
        )
        self.parcela_do_outro = self.locacao_do_outro.parcelas.get(numero=1)

        contrato_b = salvar_contrato(
            self.novo_contrato(
                empresa=self.outra, imovel=self.imovel_b, cliente=self.cliente_b, corretor=self.corretor_b
            ),
            self.gestor_b,
        )
        self.contrato_b = assinar_contrato(contrato_b, self.gestor_b, ONTEM)
        self.parcela_b = self.contrato_b.parcelas.get(numero=1)

    def baixa(self, valor, parcela=None, comprovante=None, data=ONTEM, forma=Baixa.Forma.PIX, usuario=None):
        nova = Baixa(
            parcela=parcela or self.parcela, data_pagamento=data, valor_pago=Decimal(valor), forma=forma,
            comprovante=comprovante if comprovante is not None else gerar_pdf(),
        )
        return registrar_baixa(nova, usuario or self.gestor)

    def recarregar(self, parcela=None):
        return Parcela.objects.com_pagamentos().get(pk=(parcela or self.parcela).pk)


class RegrasDaBaixaTests(BaixaBase, TestCase):
    def test_pagamento_parcial_deixa_saldo(self):
        self.baixa("10000.00")
        parcela = self.recarregar()
        self.assertEqual(parcela.total_pago, Decimal("10000.00"))
        self.assertEqual(parcela.saldo, Decimal("20000.00"))
        self.assertFalse(parcela.paga)

    def test_varias_baixas_somam_e_a_parcela_fica_paga_quando_cobre_o_valor(self):
        self.baixa("10000.00")
        self.baixa("19999.99", forma=Baixa.Forma.BOLETO)
        self.assertFalse(self.recarregar().paga)
        self.baixa("0.01", forma=Baixa.Forma.DINHEIRO)
        parcela = self.recarregar()
        self.assertTrue(parcela.paga)
        self.assertEqual(parcela.saldo, Decimal("0.00"))
        self.assertEqual(parcela.baixas.count(), 3)

    def test_pagamento_a_mais_e_aceito_e_o_saldo_nao_fica_negativo(self):
        self.baixa("35000.00")
        parcela = self.recarregar()
        self.assertTrue(parcela.paga)
        self.assertEqual(parcela.total_pago, Decimal("35000.00"))
        self.assertEqual(parcela.saldo, Decimal("0"))

    def test_sem_anotacao_o_calculo_da_o_mesmo_resultado(self):
        self.baixa("12345.67")
        parcela = Parcela.objects.get(pk=self.parcela.pk)  # sem com_pagamentos()
        self.assertEqual(parcela.saldo, Decimal("17654.33"))
        self.assertFalse(parcela.paga)

    def test_lista_de_parcelas_com_pagamentos_numa_consulta_so(self):
        self.baixa("100.00")
        with self.assertNumQueries(1):
            totais = [p.total_pago for p in self.contrato.parcelas.com_pagamentos()]
        self.assertEqual(totais, [Decimal("100.00"), Decimal("0"), Decimal("0")])

    def test_contrato_em_rascunho_nao_aceita_baixa(self):
        rascunho = salvar_contrato(self.novo_contrato(), self.gestor)
        with self.assertRaises(ValidationError) as erro:
            self.baixa("100.00", parcela=rascunho.parcelas.first())
        self.assertIn("contrato assinado", str(erro.exception))
        self.assertFalse(Baixa.objects.exists())

    def test_data_no_futuro_e_recusada(self):
        with self.assertRaises(ValidationError) as erro:
            self.baixa("100.00", data=HOJE + timedelta(days=1))
        self.assertIn("data_pagamento", erro.exception.message_dict)

    def test_hoje_e_aceito(self):
        self.assertEqual(self.baixa("100.00", data=HOJE).data_pagamento, HOJE)

    def test_valor_zero_ou_negativo_e_recusado(self):
        for valor in ("0.00", "-10.00"):
            with self.subTest(valor=valor), self.assertRaises(ValidationError) as erro:
                self.baixa(valor)
            self.assertIn("valor_pago", erro.exception.message_dict)
        self.assertFalse(Baixa.objects.exists())

    def test_comprovante_e_obrigatorio(self):
        with self.assertRaises(ValidationError) as erro:
            self.baixa("100.00", comprovante="")
        self.assertIn("comprovante", erro.exception.message_dict)

    def test_comprovante_invalido_e_recusado_e_nada_vai_para_o_disco(self):
        falso = SimpleUploadedFile("pix.pdf", b"MZ\x90\x00 programa", content_type="application/pdf")
        with self.assertRaises(ValidationError) as erro:
            self.baixa("100.00", comprovante=falso)
        self.assertIn("comprovante", erro.exception.message_dict)
        self.assertEqual(list(Path(self.pasta).rglob("*.*")), [])

    def test_comprovante_fica_na_pasta_privada_sem_url(self):
        baixa = self.baixa("100.00", comprovante=gerar_png(nome="pix Joao 529.982.247-25.png"))
        self.assertRegex(baixa.comprovante.name, rf"^contratos/{self.empresa.pk}/comprovantes/[0-9a-f]{{32}}\.png$")
        self.assertTrue(Path(baixa.comprovante.path).resolve().is_relative_to(Path(self.pasta).resolve()))
        with self.assertRaises(ValueError):
            baixa.comprovante.url

    def test_baixa_pertence_a_empresa_da_parcela_e_guarda_quem_registrou(self):
        baixa = self.baixa("100.00")
        self.assertEqual(baixa.empresa_id, self.empresa.pk)
        self.assertEqual(baixa.registrada_por, self.gestor)

    def test_contrato_com_baixa_continua_travado(self):
        self.baixa("100.00")
        self.contrato.refresh_from_db()
        self.contrato.valor = Decimal("1.00")
        with self.assertRaises(ValidationError):
            salvar_contrato(self.contrato, self.gestor)


class PainelBaixaTests(BaixaBase, TestCase):
    def url_parcela(self, parcela=None):
        parcela = parcela or self.parcela
        return reverse("contratos:parcela_detail", args=[parcela.contrato_id, parcela.pk])

    def url_comprovante(self, baixa):
        return reverse(
            "contratos:comprovante_baixar", args=[baixa.parcela.contrato_id, baixa.parcela_id, baixa.pk]
        )

    def postar(self, usuario, parcela=None, **extra):
        self.client.force_login(usuario)
        dados = {
            "data_pagamento": ONTEM.strftime("%d/%m/%Y"), "valor_pago": "30000.00", "forma": "PIX",
            "comprovante": gerar_pdf(), "observacao": "",
        }
        dados.update(extra)
        return self.client.post(self.url_parcela(parcela), dados)

    def test_gestor_registra_baixa(self):
        response = self.postar(self.gestor)
        self.assertRedirects(response, self.url_parcela(), fetch_redirect_response=False)
        baixa = Baixa.objects.get()
        self.assertEqual(baixa.valor_pago, Decimal("30000.00"))
        self.assertEqual(baixa.registrada_por, self.gestor)
        self.assertTrue(self.recarregar().paga)

    def test_erro_aparece_na_tela_e_nada_e_gravado(self):
        futuro = (HOJE + timedelta(days=3)).strftime("%d/%m/%Y")
        response = self.postar(self.gestor, data_pagamento=futuro)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "não pode estar no futuro")
        self.assertFalse(Baixa.objects.exists())

    def test_tela_da_parcela_mostra_baixas_e_saldo(self):
        baixa = self.baixa("10000.00")
        self.client.force_login(self.gestor)
        response = self.client.get(self.url_parcela())
        self.assertContains(response, "10.000,00")
        self.assertContains(response, "20.000,00")  # saldo
        self.assertContains(response, self.url_comprovante(baixa))
        self.assertNotContains(response, baixa.comprovante.name)
        self.assertContains(response, "Registrar baixa")

    def test_corretor_do_contrato_ve_mas_nao_registra(self):
        self.client.force_login(self.corretor)
        response = self.client.get(self.url_parcela())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Registrar baixa")
        self.assertEqual(self.postar(self.corretor).status_code, 403)
        self.assertFalse(Baixa.objects.exists())

    def test_quem_nao_e_do_contrato_cai_em_404(self):
        for usuario, parcela in ((self.corretor, self.parcela_do_outro), (self.gestor_b, self.parcela)):
            with self.subTest(usuario=usuario.email):
                self.client.force_login(usuario)
                self.assertEqual(self.client.get(self.url_parcela(parcela)).status_code, 404)
        self.assertEqual(self.postar(self.gestor_b).status_code, 404)

    def test_parcela_de_outro_contrato_pela_url_do_meu_contrato_da_404(self):
        self.client.force_login(self.gestor)
        url = reverse("contratos:parcela_detail", args=[self.contrato.pk, self.parcela_do_outro.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_administrador_nao_entra(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url_parcela()).status_code, 403)

    def test_comprovante_baixado_por_gestor_e_corretor_do_contrato(self):
        baixa = self.baixa("100.00")
        for usuario in (self.gestor, self.corretor):
            with self.subTest(perfil=usuario.perfil):
                self.client.force_login(usuario)
                response = self.client.get(self.url_comprovante(baixa))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(b"".join(response.streaming_content), CONTEUDO_PDF)
                self.assertIn("attachment", response["Content-Disposition"])
                self.assertIn("no-store", response["Cache-Control"])

    def test_comprovante_barrado_para_quem_nao_pode(self):
        baixa = self.baixa("100.00")
        casos = [(self.corretor2, 404), (self.gestor_b, 404), (self.admin, 403)]
        for usuario, esperado in casos:
            with self.subTest(usuario=usuario.email):
                self.client.force_login(usuario)
                self.assertEqual(self.client.get(self.url_comprovante(baixa)).status_code, esperado)
        self.client.logout()
        response = self.client.get(self.url_comprovante(baixa))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("contas:login"), response["Location"])

    def test_detalhe_do_contrato_mostra_pago_e_saldo(self):
        self.baixa("10000.00")
        self.client.force_login(self.gestor)
        response = self.client.get(reverse("contratos:contrato_detail", args=[self.contrato.pk]))
        self.assertContains(response, self.url_parcela())
        self.assertContains(response, "20.000,00")


class ApiBaixaTests(BaixaBase, APITestCase):
    def enviar(self, usuario, parcela=None, **extra):
        self.client.force_authenticate(usuario)
        dados = {
            "parcela": (parcela or self.parcela).pk, "data_pagamento": ONTEM.isoformat(),
            "valor_pago": "10000.00", "forma": "TRANSFERENCIA", "comprovante": gerar_pdf(),
        }
        dados.update(extra)
        return self.client.post(reverse("baixa-list"), dados, format="multipart")

    def test_gestor_registra_e_resposta_nao_tem_endereco_do_arquivo(self):
        response = self.enviar(self.gestor)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        baixa = Baixa.objects.get()
        self.assertNotIn("comprovante", response.data)
        self.assertNotIn(baixa.comprovante.name, str(response.data))
        self.assertTrue(response.data["url_comprovante"].endswith(
            reverse("baixa-comprovante", args=[baixa.pk])
        ))
        self.assertEqual(response.data["registrada_por"], self.gestor.nome)

    def test_erros_de_regra_voltam_como_400(self):
        response = self.enviar(self.gestor, data_pagamento=(HOJE + timedelta(days=1)).isoformat())
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("data_pagamento", response.data)
        response = self.enviar(self.gestor, comprovante=SimpleUploadedFile("x.pdf", b"nada"))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("comprovante", response.data)
        self.assertFalse(Baixa.objects.exists())

    def test_corretor_nao_registra(self):
        self.assertEqual(self.enviar(self.corretor).status_code, status.HTTP_403_FORBIDDEN)

    def test_parcela_de_outra_empresa_nao_existe(self):
        response = self.enviar(self.gestor_b, parcela=self.parcela)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("parcela", response.data)
        self.assertFalse(Baixa.objects.exists())

    def test_lista_so_o_que_o_usuario_pode_ver(self):
        minha = self.baixa("10.00")
        self.baixa("10.00", parcela=self.parcela_do_outro)
        self.baixa("10.00", parcela=self.parcela_b, usuario=self.gestor_b)
        self.client.force_authenticate(self.corretor)
        self.assertEqual([b["id"] for b in self.client.get(reverse("baixa-list")).data], [minha.pk])
        self.client.force_authenticate(self.gestor)
        self.assertEqual(len(self.client.get(reverse("baixa-list")).data), 2)
        resposta = self.client.get(reverse("baixa-list"), {"parcela": self.parcela.pk})
        self.assertEqual([b["id"] for b in resposta.data], [minha.pk])

    def test_comprovante_pela_api(self):
        baixa = self.baixa("10.00")
        casos = [
            (self.gestor, status.HTTP_200_OK), (self.corretor, status.HTTP_200_OK),
            (self.corretor2, status.HTTP_404_NOT_FOUND), (self.gestor_b, status.HTTP_404_NOT_FOUND),
            (self.admin, status.HTTP_403_FORBIDDEN),
        ]
        for usuario, esperado in casos:
            with self.subTest(usuario=usuario.email):
                self.client.force_authenticate(usuario)
                self.assertEqual(self.client.get(reverse("baixa-comprovante", args=[baixa.pk])).status_code, esperado)
        self.client.force_authenticate(None)
        response = self.client.get(reverse("baixa-comprovante", args=[baixa.pk]))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_baixa_nao_e_editada_nem_apagada(self):
        baixa = self.baixa("10.00")
        self.client.force_authenticate(self.gestor)
        url = reverse("baixa-detail", args=[baixa.pk])
        self.assertEqual(self.client.patch(url, {"valor_pago": "1.00"}).status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)

    def test_contrato_traz_pago_e_saldo_de_cada_parcela(self):
        self.baixa("10000.00")
        self.client.force_authenticate(self.gestor)
        parcelas = self.client.get(reverse("contrato-detail", args=[self.contrato.pk])).data["parcelas"]
        self.assertEqual(parcelas[0]["total_pago"], "10000.00")
        self.assertEqual(parcelas[0]["saldo"], "20000.00")
        self.assertFalse(parcelas[0]["paga"])
        self.assertEqual(parcelas[1]["total_pago"], "0.00")
