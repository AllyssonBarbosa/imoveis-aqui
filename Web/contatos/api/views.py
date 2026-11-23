from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.mixins import EmpresaScopedQuerySetMixin
from core.permissions import PertenceAUmaEmpresa
from imoveis.models import Imovel
from imoveis.vitrine import FiltroInvalido, imoveis_da_vitrine

from ..models import Contato
from ..servicos import (
    CanalIndisponivel, contatos_visiveis_para, filtrar_contatos, registrar_contato,
    restringir_ao_perfil,
)
from .serializers import ContatoSerializer, NovoContatoSerializer


class ContatoPublicoAPIView(APIView):
    """POST público do app: grava o contato e devolve o link para o app abrir.

    POST (e não GET) para que robôs e pré-carregamento não gerem contatos falsos.
    Sem login e sem token, como a vitrine da W09: authentication_classes vazio
    também dispensa o CSRF (não há sessão envolvida).
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, pk):
        # só imóvel publicado de empresa ativa recebe contato — rascunho cai em 404
        imovel = get_object_or_404(
            imoveis_da_vitrine().select_related("empresa", "corretor_responsavel__empresa"), pk=pk
        )
        serializer = NovoContatoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        canal = serializer.validated_data["canal"]
        url_do_anuncio = request.build_absolute_uri(reverse("site_publico:imovel", args=[imovel.pk]))
        try:
            _, link = registrar_contato(imovel, canal, url_do_anuncio)
        except CanalIndisponivel as erro:
            return Response({"canal": [str(erro)]}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"canal": canal, "link": link}, status=status.HTTP_201_CREATED)


CONTATOS_POR_PAGINA = 20


class PaginacaoContatos(PageNumberPagination):
    """Caixa do app: 20 por página, como o acervo e as vitrines (D-12).

    O cliente não escolhe o tamanho da página: sem `page_size_query_param`.
    """

    page_size = CONTATOS_POR_PAGINA


class ContatoViewSet(
    EmpresaScopedQuerySetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Caixa de contatos do app: lista paginada (?imovel=&de=&ate=&page=), detalhe e PATCH do retorno.

    O EmpresaScopedQuerySetMixin filtra pela empresa; depois o perfil corta
    o resto (corretor só vê os dele). Contato de fora cai em 404.
    """

    serializer_class = ContatoSerializer
    permission_classes = [PertenceAUmaEmpresa]
    queryset = Contato.objects.select_related("imovel", "corretor")
    # só `list` pagina: retrieve/PATCH e as @action não chamam o paginador
    pagination_class = PaginacaoContatos
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        queryset = restringir_ao_perfil(super().get_queryset(), self.request.user)
        if self.action != "list":
            return queryset
        try:
            return filtrar_contatos(queryset, self.request.query_params)
        except FiltroInvalido as erro:
            raise ValidationError(erro.erros)

    @action(detail=False, methods=["get"])
    def resumo(self, request):
        """Critério 6: quantos contatos visíveis ainda estão sem retorno; ignora os filtros (D-05)."""
        sem_retorno = contatos_visiveis_para(request.user).filter(retorno=Contato.Retorno.PENDENTE).count()
        return Response({"sem_retorno": sem_retorno})

    @action(detail=False, methods=["get"])
    def imoveis(self, request):
        """Opções do filtro de imóvel: só os imóveis dos contatos visíveis, por código (D-19)."""
        imoveis = Imovel.objects.filter(pk__in=contatos_visiveis_para(request.user).values("imovel"))
        return Response(list(imoveis.order_by("codigo").values("id", "codigo", "titulo")))
