from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Prefetch
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from contas.models import Usuario
from core.mixins import EmpresaScopedQuerySetMixin
from core.permissions import EhCorretor, EhGestor
from core.throttles import ThrottleVitrinePublica
from empresas.models import Empresa
from localizacao.models import Cidade

from ..models import (
    Caracteristica,
    DocumentoImovel,
    FotoImovel,
    Imovel,
    Loteamento,
    Predio,
    Quadra,
)
from ..vitrine import (
    IMOVEIS_POR_PAGINA,
    FiltroInvalido,
    buscar_na_vitrine,
    imoveis_da_empresa,
    imoveis_da_vitrine,
    imoveis_do_corretor,
)
from .serializers import (
    SERIALIZER_POR_NATUREZA,
    CaracteristicaSerializer,
    DocumentoImovelSerializer,
    EnderecoSerializer,
    FotoImovelSerializer,
    HistoricoSituacaoSerializer,
    ImovelSerializer,
    ImovelVitrineDetalheSerializer,
    ImovelVitrineSerializer,
    LoteamentoSerializer,
    MudancaSituacaoSerializer,
    PredioSerializer,
    QuadraSerializer,
)

IMOVEL_COM_DETALHES = Imovel.objects.select_related(
    "endereco", "casa", "terreno", "apartamento__predio__endereco", "lote__quadra__loteamento",
).prefetch_related("fotos")


class CaracteristicaViewSet(viewsets.ReadOnlyModelViewSet):
    """Lista de leitura — quem mantém é o administrador, pelo Django Admin."""

    serializer_class = CaracteristicaSerializer
    permission_classes = [EhCorretor]
    queryset = Caracteristica.objects.all()


class ImovelViewSet(EmpresaScopedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = ImovelSerializer
    permission_classes = [EhCorretor]
    queryset = IMOVEL_COM_DETALHES

    @action(detail=True, methods=["get", "put"])
    def endereco(self, request, pk=None):
        imovel = self.get_object()

        if request.method == "GET":
            if not imovel.endereco_do_anuncio:
                return Response(None)
            return Response(EnderecoSerializer(imovel.endereco_do_anuncio).data)

        if imovel.natureza == Imovel.Natureza.APARTAMENTO:
            return Response(
                {"detail": "O endereço do apartamento é o do prédio — altere pelo cadastro do prédio."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = EnderecoSerializer(instance=imovel.endereco, data=request.data)
        serializer.is_valid(raise_exception=True)
        endereco = serializer.save()
        if not imovel.endereco_id:
            imovel.endereco = endereco
            imovel.save(update_fields=["endereco"])
        return Response(EnderecoSerializer(endereco).data)

    @action(detail=True, methods=["get", "put"])
    def detalhes(self, request, pk=None):
        """Dados próprios da natureza: os campos de casa ou de terreno, conforme o imóvel."""
        imovel = self.get_object()
        serializer_class = SERIALIZER_POR_NATUREZA[imovel.natureza]

        if request.method == "GET":
            if imovel.detalhes is None:
                return Response(None)
            return Response(serializer_class(imovel.detalhes).data)

        serializer = serializer_class(
            instance=imovel.detalhes, data=request.data, context={"imovel": imovel}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # publicar saiu daqui na W08: publicação e situação são do gestor (GestaoImovelViewSet)


class PredioViewSet(EmpresaScopedQuerySetMixin, viewsets.ModelViewSet):
    """Prédios da própria empresa. Alterar aqui reflete em todos os apartamentos ligados."""

    serializer_class = PredioSerializer
    permission_classes = [EhCorretor]
    queryset = Predio.objects.select_related("endereco")

    def destroy(self, request, *args, **kwargs):
        predio = self.get_object()
        if predio.apartamentos.exists():
            return Response(
                {"detail": "Este prédio tem apartamentos ligados e não pode ser excluído."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        endereco = predio.endereco
        predio.delete()
        endereco.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LoteamentoViewSet(EmpresaScopedQuerySetMixin, viewsets.ModelViewSet):
    """Loteamentos da própria empresa, com o resumo de lotes por quadra."""

    serializer_class = LoteamentoSerializer
    permission_classes = [EhCorretor]
    queryset = Loteamento.objects.prefetch_related("quadras__lotes__imovel")

    def destroy(self, request, *args, **kwargs):
        loteamento = self.get_object()
        if Quadra.objects.filter(loteamento=loteamento, lotes__isnull=False).exists():
            return Response(
                {"detail": "Este loteamento tem lotes cadastrados e não pode ser excluído."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)


class QuadraViewSet(viewsets.ModelViewSet):
    """Quadras — filtradas por ?loteamento=<id>. Isoladas pela empresa do loteamento."""

    serializer_class = QuadraSerializer
    permission_classes = [EhCorretor]
    queryset = Quadra.objects.prefetch_related("lotes__imovel")

    def get_queryset(self):
        queryset = super().get_queryset().filter(loteamento__empresa=self.request.user.empresa)
        loteamento_id = self.request.query_params.get("loteamento")
        if loteamento_id:
            queryset = queryset.filter(loteamento_id=loteamento_id)
        return queryset

    def destroy(self, request, *args, **kwargs):
        quadra = self.get_object()
        if quadra.lotes.exists():
            return Response(
                {"detail": "Esta quadra tem lotes cadastrados e não pode ser excluída."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)


class FotoImovelViewSet(viewsets.ModelViewSet):
    """Fotos do imóvel — filtradas por ?imovel=<id>. Aceita várias de uma vez."""

    serializer_class = FotoImovelSerializer
    permission_classes = [EhCorretor]
    queryset = FotoImovel.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset().filter(imovel__empresa=self.request.user.empresa)
        imovel_id = self.request.query_params.get("imovel")
        if imovel_id:
            queryset = queryset.filter(imovel_id=imovel_id)
        return queryset

    def create(self, request, *args, **kwargs):
        imovel = get_object_or_404(
            Imovel, pk=request.data.get("imovel"), empresa=request.user.empresa
        )
        arquivos = request.FILES.getlist("imagens") or request.FILES.getlist("imagem")
        if not arquivos:
            return Response({"imagens": ["Envie ao menos uma imagem."]}, status=status.HTTP_400_BAD_REQUEST)

        proxima_ordem = imovel.fotos.count()
        criadas = []
        try:
            for indice, arquivo in enumerate(arquivos):
                foto = FotoImovel(imovel=imovel, imagem=arquivo, ordem=proxima_ordem + indice)
                foto.full_clean()
                foto.save()
                criadas.append(foto)
        except DjangoValidationError as erro:
            return Response({"imagens": erro.messages}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(criadas, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def definir_capa(self, request, pk=None):
        foto = self.get_object()
        foto.definir_como_capa()
        return Response(self.get_serializer(foto).data)


class GestaoImovelViewSet(EmpresaScopedQuerySetMixin, viewsets.ReadOnlyModelViewSet):
    """Visão do gestor sobre os imóveis da empresa: publicação, situação e histórico (W08)."""

    serializer_class = ImovelSerializer
    permission_classes = [EhGestor]
    queryset = IMOVEL_COM_DETALHES

    def get_queryset(self):
        queryset = super().get_queryset()
        situacao = self.request.query_params.get("situacao")
        if situacao:
            queryset = queryset.filter(situacao=situacao)
        return queryset

    @action(detail=True, methods=["post"])
    def publicar(self, request, pk=None):
        imovel = self.get_object()
        try:
            imovel.publicar()
        except DjangoValidationError as erro:
            return Response(
                {"detail": erro.messages[0], "pendencias": imovel.pendencias_para_publicar()},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(self.get_serializer(imovel).data)

    @action(detail=True, methods=["post"])
    def despublicar(self, request, pk=None):
        imovel = self.get_object()
        imovel.despublicar()
        return Response(self.get_serializer(imovel).data)

    @action(detail=True, methods=["post"], serializer_class=MudancaSituacaoSerializer)
    def situacao(self, request, pk=None):
        imovel = self.get_object()
        entrada = MudancaSituacaoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        try:
            imovel.mudar_situacao(
                entrada.validated_data["situacao"], request.user,
                entrada.validated_data["observacao"],
            )
        except DjangoValidationError as erro:
            return Response({"situacao": erro.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ImovelSerializer(imovel, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["get"])
    def historico(self, request, pk=None):
        imovel = self.get_object()
        mudancas = imovel.historico_situacao.select_related("alterado_por")
        return Response(HistoricoSituacaoSerializer(mudancas, many=True).data)


class DocumentoImovelViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    """Documentos do imóvel, só do gestor — filtrados por ?imovel=<id> (W08)."""

    serializer_class = DocumentoImovelSerializer
    permission_classes = [EhGestor]
    parser_classes = [MultiPartParser, FormParser]
    queryset = DocumentoImovel.objects.select_related("enviado_por")

    def get_queryset(self):
        queryset = super().get_queryset().filter(imovel__empresa=self.request.user.empresa)
        imovel_id = self.request.query_params.get("imovel")
        if imovel_id:
            queryset = queryset.filter(imovel_id=imovel_id)
        return queryset

    def perform_create(self, serializer):
        serializer.save(enviado_por=self.request.user)

    def perform_destroy(self, documento):
        documento.arquivo.delete(save=False)
        documento.delete()

    @action(detail=True, methods=["get"])
    def baixar(self, request, pk=None):
        documento = self.get_object()  # já filtrado pela empresa
        return FileResponse(documento.arquivo.open("rb"), as_attachment=True)


# --- Vitrine pública (W09) ---------------------------------------------------


class PaginacaoVitrine(PageNumberPagination):
    page_size = IMOVEIS_POR_PAGINA


class VitrineCidadeAPIView(generics.ListAPIView):
    """Busca da vitrine, sempre dentro de uma cidade. Pública: sem login e sem token.

    authentication_classes vazio: nem um token vencido no app impede a consulta.
    Os filtros e a ordenação ficam em imoveis/vitrine.py — os mesmos do site.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ThrottleVitrinePublica]
    serializer_class = ImovelVitrineSerializer
    pagination_class = PaginacaoVitrine

    def get_queryset(self):
        cidade = get_object_or_404(Cidade, pk=self.kwargs["cidade_id"])
        try:
            return buscar_na_vitrine(cidade, self.request.query_params)
        except FiltroInvalido as erro:
            raise ValidationError(erro.erros)


class VitrineCorretorAPIView(generics.ListAPIView):
    """Imóveis na vitrine de um corretor (07.1, D-10). Pública: sem login e sem token.

    Mesmo item, paginação e limite de taxa da busca da cidade, mas sem filtro, busca nem
    ordenação: só `page` é lido (pelo paginador), mais recentes primeiro. Corretor inexistente,
    desativado, que não é corretor ou de empresa inativa dá o mesmo 404 — não se sabe qual dos
    casos foi. Corretor ativo sem imóveis na vitrine dá 200 com a lista vazia.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ThrottleVitrinePublica]
    serializer_class = ImovelVitrineSerializer
    pagination_class = PaginacaoVitrine

    def get_queryset(self):
        corretor = get_object_or_404(
            Usuario, pk=self.kwargs["pk"], perfil=Usuario.Perfil.CORRETOR,
            is_active=True, empresa__ativa=True,
        )
        return imoveis_do_corretor(corretor)


class VitrineEmpresaAPIView(generics.ListAPIView):
    """Imóveis na vitrine de uma empresa, de todos os corretores dela (07.1, D-10). Pública.

    Mesmas regras da vitrine do corretor: só `page` é lido, mais recentes primeiro. Empresa
    inexistente ou inativa dá 404; empresa ativa sem imóveis na vitrine dá 200 com a lista vazia.
    O imóvel de um corretor desativado continua aparecendo aqui (como na vitrine da cidade).
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ThrottleVitrinePublica]
    serializer_class = ImovelVitrineSerializer
    pagination_class = PaginacaoVitrine

    def get_queryset(self):
        empresa = get_object_or_404(Empresa, pk=self.kwargs["pk"], ativa=True)
        return imoveis_da_empresa(empresa)


class BairrosDaCidadeAPIView(APIView):
    """Opções de bairro da vitrine — sem login; derivadas da mesma imoveis_da_vitrine().

    Como saem da mesma regra da lista, escolher uma opção nunca leva a uma busca vazia.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ThrottleVitrinePublica]

    def get(self, request, cidade_id):
        cidade = get_object_or_404(Cidade, pk=cidade_id)
        bairros = (
            imoveis_da_vitrine()
            .filter(cidade_anuncio=cidade.pk)
            .exclude(bairro_anuncio__isnull=True)
            .exclude(bairro_anuncio="")
            .order_by("bairro_anuncio")
            .values_list("bairro_anuncio", flat=True)
            .distinct()
        )
        return Response(list(bairros))


class CaracteristicasPublicasAPIView(generics.ListAPIView):
    """Catálogo global de características (id e nome), sem login — alimenta o filtro do app.

    Sem paginação: o catálogo é curto e o app precisa dele inteiro para montar as opções.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ThrottleVitrinePublica]
    pagination_class = None
    serializer_class = CaracteristicaSerializer
    queryset = Caracteristica.objects.all()  # Meta.ordering = ["nome"]


class ImovelVitrineDetalheAPIView(generics.RetrieveAPIView):
    """Página pública do imóvel. Rascunho ou empresa inativa cai em 404."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ThrottleVitrinePublica]
    serializer_class = ImovelVitrineDetalheSerializer

    def get_queryset(self):
        return imoveis_da_vitrine().select_related(
            "endereco__cidade", "corretor_responsavel__empresa", "casa", "terreno",
            "apartamento__predio__endereco__cidade", "lote__quadra__loteamento",
        ).prefetch_related(
            # a capa abre a galeria (regra da API; o app não reordena) — depois ordem e id
            Prefetch("fotos", queryset=FotoImovel.objects.order_by("-capa", "ordem", "id")),
            "caracteristicas",
        )
