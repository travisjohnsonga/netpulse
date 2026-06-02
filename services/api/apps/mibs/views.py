"""
MIB management API.

  GET    /api/mibs/                 list loaded MIBs grouped metadata
  POST   /api/mibs/upload/          upload a MIB (validated, saved to custom/)
  DELETE /api/mibs/<name>/          delete a custom MIB (custom-only)
  POST   /api/mibs/<name>/reload/   clear the index cache (re-scan)
  GET    /api/mibs/resolve/<oid>/   resolve a numeric OID to a name
"""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import AdminOnly

from . import index


class MibListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"mibs": index.list_mibs()})


class MibUploadView(APIView):
    permission_classes = [AdminOnly]

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"error": "no file provided (field 'file')"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            text = upload.read().decode("utf-8", errors="replace")
        except Exception:
            return Response({"error": "could not read file"}, status=status.HTTP_400_BAD_REQUEST)
        result = index.save_upload(upload.name, text)
        if not result.get("success"):
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_201_CREATED)


class MibDetailView(APIView):
    permission_classes = [AdminOnly]

    def delete(self, request, name):
        if index.delete_mib(name):
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(
            {"error": "not found, or not a custom MIB (standard/vendor MIBs can't be deleted)"},
            status=status.HTTP_404_NOT_FOUND)


class MibReloadView(APIView):
    permission_classes = [AdminOnly]

    def post(self, request, name=None):
        index.reload()
        return Response({"reloaded": True})


class MibResolveView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, oid):
        return Response(index.resolve_oid(oid))
