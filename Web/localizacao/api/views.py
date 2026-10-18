from rest_framework import generics
from rest_framework.permissions import AllowAny

from empresas.api.serializers import CidadeSerializer
from imoveis.vitrine import cidades_atendidas


class CidadeAtendidaAPIView(generics.ListAPIView):
    """Cidades atendidas pela vitrine, sem login: só as que têm imóvel publicado.

    Usa a mesma regra da busca (imoveis_da_vitrine), então o visitante
    nunca escolhe uma cidade que não devolve nada.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = CidadeSerializer

    def get_queryset(self):
        return cidades_atendidas()
