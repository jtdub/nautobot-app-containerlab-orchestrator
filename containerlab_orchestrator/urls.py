"""Django urlpatterns declaration for containerlab_orchestrator app."""

from django.templatetags.static import static
from django.urls import path
from django.views.generic import RedirectView
from nautobot.apps.urls import NautobotUIViewSetRouter

from containerlab_orchestrator import views

app_name = "containerlab_orchestrator"
router = NautobotUIViewSetRouter()
router.register("containerlab-hosts", views.ContainerlabHostUIViewSet)
router.register("node-mappings", views.NodeMappingUIViewSet)


urlpatterns = [
    path("docs/", RedirectView.as_view(url=static("containerlab_orchestrator/docs/index.html")), name="docs"),
]

urlpatterns += router.urls
