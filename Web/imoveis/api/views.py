from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.mixins import EmpresaScopedQuerySetMixin
from core.permissions import EhCorretor

from ..models import Caracteristica, FotoImovel, Imovel
from .serializers import (
    CaracteristicaSerializer,
    EnderecoSerializer,
    FotoImovelSerializer,
    ImovelSerializer,
)


class CaracteristicaViewSet(viewsets.ReadOnlyModelViewSet):
    """Lista de leitura — quem mantém é o administrador, pelo Django Admin."""

    serializer_class = CaracteristicaSerializer
    permission_classes = [EhCorretor]
    queryset = Caracteristica.objects.all()


class ImovelViewSet(EmpresaScopedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = ImovelSerializer
    permission_classes = [EhCorretor]
    queryset = Imovel.objects.all()

    @action(detail=True, methods=["get", "put"])
    def endereco(self, request, pk=None):
        imovel = self.get_object()

        if request.method == "GET":
            if not imovel.endereco:
                return Response(None)
            return Response(EnderecoSerializer(imovel.endereco).data)

        serializer = EnderecoSerializer(instance=imovel.endereco, data=request.data)
        serializer.is_valid(raise_exception=True)
        endereco = serializer.save()
        if not imovel.endereco_id:
            imovel.endereco = endereco
            imovel.save(update_fields=["endereco"])
        return Response(EnderecoSerializer(endereco).data)

    @action(detail=True, methods=["post"])
    def publicar(self, request, pk=None):
        imovel = self.get_object()
        try:
            imovel.publicar()
        except DjangoValidationError as erro:
            return Response({"detail": erro.messages[0]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(imovel).data)


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
