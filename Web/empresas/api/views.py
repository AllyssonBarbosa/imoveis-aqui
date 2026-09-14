from rest_framework import generics
from rest_framework.permissions import AllowAny

from ..models import Empresa
from .serializers import EmpresaPublicaSerializer


class EmpresaPublicaAPIView(generics.RetrieveAPIView):
    """Perfil público da empresa, sem login. Empresa inativa não aparece."""

    permission_classes = [AllowAny]
    serializer_class = EmpresaPublicaSerializer
    queryset = Empresa.objects.filter(ativa=True)
