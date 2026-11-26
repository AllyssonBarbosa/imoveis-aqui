"""Fotos do imóvel pelas rotas aninhadas `/api/imoveis/<id>/fotos/` (APP 11).

Cobre o serviço `imoveis.fotos` (ordem, capa única, teto, reordenação, exclusão), as rotas
(isolamento por empresa, perfis, envio de uma foto por requisição, respostas) e o critério 6:
o que o corretor grava aparece igual na vitrine (API pública) e no site.
"""

import io

from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from contas.models import Usuario

from . import fotos as servico
from .models import FotoImovel
from .tests import (
    MediaTemporariaMixin,
    VitrineBase,
    criar_corretor,
    criar_empresa,
    criar_gestor,
    criar_imovel,
    criar_proprietario,
)


def imagem_jpeg(nome="foto.jpg", cor="green"):
    buffer = io.BytesIO()
    Image.new("RGB", (12, 8), color=cor).save(buffer, format="JPEG")
    return SimpleUploadedFile(nome, buffer.getvalue(), content_type="image/jpeg")


def texto_disfarcado_de_jpeg():
    return SimpleUploadedFile("foto.jpg", b"isto nao e uma imagem", content_type="image/jpeg")


def arquivo_grande_demais():
    # O tamanho é conferido antes do formato, então bytes quaisquer servem.
    return SimpleUploadedFile(
        "foto.jpg", b"\0" * (5 * 1024 * 1024 + 1), content_type="image/jpeg"
    )


def ordens_e_capas(imovel):
    return list(
        FotoImovel.objects.filter(imovel=imovel).order_by("ordem", "id").values_list(
            "pk", "ordem", "capa"
        )
    )


class BaseFotos(MediaTemporariaMixin):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.outro_imovel = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="IM002"
        )

    def enviar(self, quantidade, imovel=None):
        return [servico.adicionar_foto(imovel or self.imovel, imagem_jpeg()) for _ in range(quantidade)]


class ServicoFotosTests(BaseFotos, TestCase):
    def test_primeira_foto_vira_capa_e_as_seguintes_entram_no_fim(self):
        primeira, segunda, terceira = self.enviar(3)
        self.assertEqual(
            ordens_e_capas(self.imovel),
            [(primeira.pk, 0, True), (segunda.pk, 1, False), (terceira.pk, 2, False)],
        )

    def test_ordem_nova_e_maior_mais_um_e_nao_a_contagem(self):
        primeira, _, _ = self.enviar(3)
        # apaga a de ordem 0 sem renumerar (como a rota antiga deixava): ordens 1 e 2 sobram
        FotoImovel.objects.filter(pk=primeira.pk).delete()
        nova = servico.adicionar_foto(self.imovel, imagem_jpeg())
        self.assertEqual(nova.ordem, 3)  # count() daria 2 e repetiria a ordem 2

    def test_imovel_com_fotos_sem_capa_ganha_capa_na_proxima_foto(self):
        FotoImovel.objects.create(imovel=self.imovel, imagem=imagem_jpeg(), ordem=0, capa=False)
        nova = servico.adicionar_foto(self.imovel, imagem_jpeg())
        self.assertTrue(nova.capa)

    def test_teto_de_30_fotos_recusa_a_31a_sem_gravar(self):
        self.enviar(servico.LIMITE_FOTOS_POR_IMOVEL)
        with self.assertRaises(ValidationError) as contexto:
            servico.adicionar_foto(self.imovel, imagem_jpeg())
        self.assertEqual(
            contexto.exception.message_dict,
            {"imagem": ["Este imóvel já tem 30 fotos, o máximo permitido."]},
        )
        self.assertEqual(self.imovel.fotos.count(), 30)

    def test_arquivo_que_nao_e_imagem_e_recusado_sem_gravar(self):
        with self.assertRaises(ValidationError) as contexto:
            servico.adicionar_foto(self.imovel, texto_disfarcado_de_jpeg())
        self.assertIn("imagem", contexto.exception.message_dict)
        self.assertFalse(self.imovel.fotos.exists())

    def test_reordenar_grava_a_ordem_pedida(self):
        a, b, c = self.enviar(3)
        servico.reordenar_fotos(self.imovel, [c.pk, a.pk, b.pk])
        self.assertEqual(
            ordens_e_capas(self.imovel), [(c.pk, 0, False), (a.pk, 1, True), (b.pk, 2, False)]
        )

    def test_reordenar_com_lista_invalida_nao_muda_nada(self):
        a, b, c = self.enviar(3)
        (de_outro_imovel,) = self.enviar(1, imovel=self.outro_imovel)
        antes = ordens_e_capas(self.imovel)
        invalidas = [
            [a.pk, b.pk],  # faltando
            [a.pk, b.pk, b.pk],  # repetido
            [a.pk, b.pk, c.pk, c.pk],  # repetido com todas
            [a.pk, b.pk, de_outro_imovel.pk],  # de outro imóvel
            [],  # vazia com fotos
            [a.pk, b.pk, str(c.pk)],  # não inteiro
            [a.pk, b.pk, True],  # bool não vale como id
            None,
            "1,2,3",
        ]
        for ids in invalidas:
            with self.subTest(ids=ids), self.assertRaises(ValidationError) as contexto:
                servico.reordenar_fotos(self.imovel, ids)
            self.assertIn("ids", contexto.exception.message_dict)
        self.assertEqual(ordens_e_capas(self.imovel), antes)

    def test_definir_capa_deixa_uma_so(self):
        a, b, c = self.enviar(3)
        servico.definir_capa(c)
        servico.definir_capa(b)
        self.assertEqual(
            list(FotoImovel.objects.filter(imovel=self.imovel, capa=True).values_list("pk", flat=True)),
            [b.pk],
        )
        self.assertEqual(
            list(FotoImovel.objects.filter(imovel=self.outro_imovel, capa=True)), []
        )

    def test_apagar_foto_comum_fecha_o_buraco_na_ordem(self):
        a, b, c = self.enviar(3)
        servico.apagar_foto(b)
        self.assertEqual(ordens_e_capas(self.imovel), [(a.pk, 0, True), (c.pk, 1, False)])

    def test_apagar_a_capa_promove_a_proxima_pela_ordem(self):
        a, b, c = self.enviar(3)
        servico.reordenar_fotos(self.imovel, [c.pk, a.pk, b.pk])  # a (capa) fica no meio
        servico.apagar_foto(FotoImovel.objects.get(pk=a.pk))
        self.assertEqual(ordens_e_capas(self.imovel), [(c.pk, 0, True), (b.pk, 1, False)])

    def test_apagar_a_ultima_deixa_o_imovel_sem_fotos(self):
        (unica,) = self.enviar(1)
        servico.apagar_foto(unica)
        self.assertFalse(self.imovel.fotos.exists())

    def test_apagar_tira_o_arquivo_do_disco_so_depois_do_commit(self):
        (foto,) = self.enviar(1)
        nome = foto.imagem.name
        self.assertTrue(default_storage.exists(nome))
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            servico.apagar_foto(foto)
        self.assertTrue(default_storage.exists(nome))  # ainda não houve commit
        for callback in callbacks:
            callback()
        self.assertFalse(default_storage.exists(nome))


class RotasFotosTests(BaseFotos, APITestCase):
    def setUp(self):
        super().setUp()
        self.colega = criar_corretor(self.empresa, email="colega@teste.com")
        self.gestor = criar_gestor(self.empresa)
        # sem senha (fica inutilizável): o teste autentica com force_authenticate
        self.administrador = Usuario.objects.create_user(
            email="admin@teste.com", nome="Admin",
            perfil=Usuario.Perfil.ADMINISTRADOR,
        )
        self.empresa_b = criar_empresa(nome="Outra Imobiliária", cnpj="11.444.777/0001-61")
        self.corretor_b = criar_corretor(self.empresa_b, email="corretor.b@teste.com")
        self.imovel_b = criar_imovel(
            self.empresa_b, self.corretor_b, criar_proprietario(self.empresa_b), codigo="B001"
        )

    # --- urls ---------------------------------------------------------------------------------
    @staticmethod
    def url_fotos(imovel):
        return reverse("imovel-fotos", args=[imovel.pk])

    @staticmethod
    def url_ordem(imovel):
        return reverse("imovel-fotos-ordem", args=[imovel.pk])

    @staticmethod
    def url_capa(imovel, foto):
        return reverse("imovel-fotos-capa", args=[imovel.pk, foto.pk])

    @staticmethod
    def url_foto(imovel, foto):
        return reverse("imovel-foto", args=[imovel.pk, foto.pk])

    def postar_imagem(self, imovel, *arquivos):
        return self.client.post(
            self.url_fotos(imovel), {"imagem": list(arquivos)}, format="multipart"
        )

    # --- quem pode ----------------------------------------------------------------------------
    def test_rotas_aceitam_token_do_corretor(self):
        token = Token.objects.create(user=self.corretor)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        self.assertEqual(self.client.get(self.url_fotos(self.imovel)).status_code, 200)

    def test_colega_da_mesma_empresa_gerencia_as_fotos(self):
        self.client.force_authenticate(self.colega)
        resposta = self.postar_imagem(self.imovel, imagem_jpeg())
        self.assertEqual(resposta.status_code, status.HTTP_201_CREATED, resposta.data)
        foto_id = resposta.data["id"]
        self.assertEqual(self.postar_imagem(self.imovel, imagem_jpeg()).status_code, 201)
        segunda_id = FotoImovel.objects.get(imovel=self.imovel, ordem=1).pk
        self.assertEqual(
            self.client.put(
                self.url_ordem(self.imovel), {"ids": [segunda_id, foto_id]}, format="json"
            ).status_code,
            200,
        )
        foto = FotoImovel.objects.get(pk=foto_id)
        segunda = FotoImovel.objects.get(pk=segunda_id)
        self.assertEqual(self.client.post(self.url_capa(self.imovel, segunda)).status_code, 200)
        self.assertEqual(self.client.delete(self.url_foto(self.imovel, foto)).status_code, 200)

    def todas_as_rotas(self, imovel, foto):
        return [
            ("get", self.url_fotos(imovel), None),
            ("post", self.url_fotos(imovel), "multipart"),
            ("put", self.url_ordem(imovel), "json"),
            ("post", self.url_capa(imovel, foto), None),
            ("delete", self.url_foto(imovel, foto), None),
        ]

    def chamar(self, metodo, url, formato):
        if formato == "multipart":
            return self.client.post(url, {"imagem": [imagem_jpeg()]}, format="multipart")
        if formato == "json":
            return self.client.put(url, {"ids": []}, format="json")
        return getattr(self.client, metodo)(url)

    def test_imovel_de_outra_empresa_da_404_nas_5_rotas_sem_mudar_nada(self):
        (foto_b,) = self.enviar(1, imovel=self.imovel_b)
        antes = ordens_e_capas(self.imovel_b)
        self.client.force_authenticate(self.corretor)
        for metodo, url, formato in self.todas_as_rotas(self.imovel_b, foto_b):
            with self.subTest(metodo=metodo, url=url):
                self.assertEqual(self.chamar(metodo, url, formato).status_code, 404)
        self.assertEqual(ordens_e_capas(self.imovel_b), antes)
        self.assertTrue(default_storage.exists(foto_b.imagem.name))

    def test_imovel_inexistente_da_404(self):
        self.client.force_authenticate(self.corretor)
        self.assertEqual(self.client.get(reverse("imovel-fotos", args=[999999])).status_code, 404)

    def test_foto_de_outro_imovel_da_mesma_empresa_da_404(self):
        (foto_do_outro,) = self.enviar(1, imovel=self.outro_imovel)
        self.client.force_authenticate(self.corretor)
        self.assertEqual(self.client.post(self.url_capa(self.imovel, foto_do_outro)).status_code, 404)
        self.assertEqual(self.client.delete(self.url_foto(self.imovel, foto_do_outro)).status_code, 404)
        self.assertTrue(FotoImovel.objects.filter(pk=foto_do_outro.pk).exists())

    def test_gestor_e_administrador_recebem_403(self):
        (foto,) = self.enviar(1)
        for usuario in (self.gestor, self.administrador):
            self.client.force_authenticate(usuario)
            for metodo, url, formato in self.todas_as_rotas(self.imovel, foto):
                with self.subTest(usuario=usuario.email, metodo=metodo, url=url):
                    self.assertEqual(self.chamar(metodo, url, formato).status_code, 403)

    def test_sem_token_recebe_401(self):
        (foto,) = self.enviar(1)
        self.client.force_authenticate(None)
        for metodo, url, formato in self.todas_as_rotas(self.imovel, foto):
            with self.subTest(metodo=metodo, url=url):
                self.assertEqual(self.chamar(metodo, url, formato).status_code, 401)

    # --- envio --------------------------------------------------------------------------------
    def test_envio_devolve_201_com_a_foto_e_url_absoluta(self):
        self.client.force_authenticate(self.corretor)
        resposta = self.postar_imagem(self.imovel, imagem_jpeg())
        self.assertEqual(resposta.status_code, status.HTTP_201_CREATED)
        self.assertEqual(set(resposta.data), {"id", "imagem", "ordem", "capa"})
        self.assertTrue(resposta.data["imagem"].startswith("http://testserver/media/imoveis/fotos/"))
        self.assertEqual((resposta.data["ordem"], resposta.data["capa"]), (0, True))

    def test_dois_arquivos_ou_nenhum_dao_400_sem_gravar(self):
        self.client.force_authenticate(self.corretor)
        resposta = self.postar_imagem(self.imovel, imagem_jpeg(), imagem_jpeg("b.jpg"))
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.data, {"imagem": ["Envie exatamente uma imagem por requisição."]})
        resposta = self.client.post(self.url_fotos(self.imovel), {}, format="multipart")
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(self.imovel.fotos.exists())

    def test_tipo_e_tamanho_invalidos_dao_400_em_imagem(self):
        self.client.force_authenticate(self.corretor)
        for arquivo in (texto_disfarcado_de_jpeg(), arquivo_grande_demais()):
            with self.subTest(arquivo=arquivo.size):
                resposta = self.postar_imagem(self.imovel, arquivo)
                self.assertEqual(resposta.status_code, 400)
                self.assertEqual(list(resposta.data), ["imagem"])
        self.assertFalse(self.imovel.fotos.exists())

    def test_31a_foto_da_400_com_a_mensagem_do_teto(self):
        self.enviar(30)
        self.client.force_authenticate(self.corretor)
        resposta = self.postar_imagem(self.imovel, imagem_jpeg())
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.data, {"imagem": ["Este imóvel já tem 30 fotos, o máximo permitido."]})

    # --- leitura, ordem, capa, exclusão --------------------------------------------------------
    def test_lista_vem_na_ordem_do_servidor(self):
        a, b, c = self.enviar(3)
        servico.reordenar_fotos(self.imovel, [b.pk, c.pk, a.pk])
        self.client.force_authenticate(self.corretor)
        resposta = self.client.get(self.url_fotos(self.imovel))
        self.assertEqual([item["id"] for item in resposta.data], [b.pk, c.pk, a.pk])
        self.assertEqual([item["ordem"] for item in resposta.data], [0, 1, 2])

    def test_reordenar_devolve_a_lista_relida(self):
        a, b, c = self.enviar(3)
        self.client.force_authenticate(self.corretor)
        resposta = self.client.put(self.url_ordem(self.imovel), {"ids": [c.pk, b.pk, a.pk]}, format="json")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(
            [(item["id"], item["ordem"]) for item in resposta.data], [(c.pk, 0), (b.pk, 1), (a.pk, 2)]
        )

    def test_reordenar_invalido_da_400_em_ids(self):
        a, b, _ = self.enviar(3)
        self.client.force_authenticate(self.corretor)
        corpos = [{"ids": [a.pk, b.pk]}, {"ids": [a.pk, a.pk, b.pk]}, {"ids": "1,2"}, {}]
        for corpo in corpos:
            with self.subTest(corpo=corpo):
                resposta = self.client.put(self.url_ordem(self.imovel), corpo, format="json")
                self.assertEqual(resposta.status_code, 400)
                self.assertEqual(list(resposta.data), ["ids"])

    def test_definir_capa_devolve_a_lista_com_uma_capa(self):
        a, b, c = self.enviar(3)
        self.client.force_authenticate(self.corretor)
        resposta = self.client.post(self.url_capa(self.imovel, c))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual([item["id"] for item in resposta.data if item["capa"]], [c.pk])

    def test_apagar_a_capa_promove_renumera_e_apaga_o_arquivo(self):
        a, b, c = self.enviar(3)
        nome = a.imagem.name
        self.client.force_authenticate(self.corretor)
        with self.captureOnCommitCallbacks(execute=True):
            resposta = self.client.delete(self.url_foto(self.imovel, a))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(
            [(item["id"], item["ordem"], item["capa"]) for item in resposta.data],
            [(b.pk, 0, True), (c.pk, 1, False)],
        )
        self.assertFalse(default_storage.exists(nome))

    def test_rota_antiga_continua_respondendo(self):
        self.enviar(1)
        self.client.force_authenticate(self.corretor)
        resposta = self.client.get(reverse("foto-imovel-list"), {"imovel": self.imovel.pk})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(set(resposta.data[0]), {"id", "imovel", "imagem", "ordem", "capa"})


class FotosNaVitrineTests(VitrineBase):
    """Critério 6 (FOT-06): o que o corretor grava pelas rotas aninhadas é o que a vitrine e o site
    mostram — mesma capa e mesma sequência (capa primeiro, depois a ordem do corretor)."""

    def setUp(self):
        super().setUp()
        FotoImovel.objects.filter(imovel=self.casa).delete()  # começa sem fotos
        self.client.force_authenticate(self.corretor)

    def enviar_pela_rota(self):
        resposta = self.client.post(
            reverse("imovel-fotos", args=[self.casa.pk]), {"imagem": [imagem_jpeg()]},
            format="multipart",
        )
        self.assertEqual(resposta.status_code, status.HTTP_201_CREATED, resposta.data)
        return FotoImovel.objects.get(pk=resposta.data["id"])

    def sequencia_publica(self):
        """URLs (sem o host) na API pública, a capa do cartão e a galeria do site."""
        def sem_host(url):
            return url.removeprefix("http://testserver")

        detalhe = self.client.get(reverse("publico-imovel", args=[self.casa.pk])).data["fotos"]
        busca = self.client.get(reverse("publico-vitrine-cidade", args=[self.sp.pk])).data["results"]
        cartao = next(item for item in busca if item["id"] == self.casa.pk)
        site = self.client.get(reverse("site_publico:imovel", args=[self.casa.pk]))
        return (
            [sem_host(foto["imagem"]) for foto in detalhe],
            sem_host(cartao["foto_capa"]),
            [sem_host(foto["imagem"]) for foto in site.context["imovel"]["fotos"]],
        )

    def test_ordem_e_capa_gravadas_pelo_corretor_aparecem_na_vitrine_e_no_site(self):
        a, b, c = self.enviar_pela_rota(), self.enviar_pela_rota(), self.enviar_pela_rota()
        self.client.put(
            reverse("imovel-fotos-ordem", args=[self.casa.pk]), {"ids": [c.pk, a.pk, b.pk]},
            format="json",
        )
        self.client.post(reverse("imovel-fotos-capa", args=[self.casa.pk, b.pk]))

        esperado = [b.imagem.url, c.imagem.url, a.imagem.url]  # capa, depois a ordem c, a
        detalhe, capa_do_cartao, site = self.sequencia_publica()
        self.assertEqual(detalhe, esperado)
        self.assertEqual(site, esperado)
        self.assertEqual(capa_do_cartao, b.imagem.url)

    def test_apagar_a_capa_muda_a_capa_na_vitrine_e_no_site(self):
        a, b, c = self.enviar_pela_rota(), self.enviar_pela_rota(), self.enviar_pela_rota()
        self.client.put(
            reverse("imovel-fotos-ordem", args=[self.casa.pk]), {"ids": [c.pk, a.pk, b.pk]},
            format="json",
        )
        self.client.post(reverse("imovel-fotos-capa", args=[self.casa.pk, b.pk]))
        self.client.delete(reverse("imovel-foto", args=[self.casa.pk, b.pk]))

        detalhe, capa_do_cartao, site = self.sequencia_publica()
        self.assertEqual(detalhe, [c.imagem.url, a.imagem.url])  # c promovida (primeira na ordem)
        self.assertEqual(site, detalhe)
        self.assertEqual(capa_do_cartao, c.imagem.url)
