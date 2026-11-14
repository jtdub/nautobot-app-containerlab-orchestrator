"""REST API URL patterns for the containerlab_orchestrator app."""

from nautobot.apps.api import OrderedDefaultRouter

from containerlab_orchestrator.api.views import (
    ContainerlabHostViewSet,
    InterfaceMappingViewSet,
    LabViewSet,
    NodeMappingViewSet,
)

router = OrderedDefaultRouter()
router.register("containerlab-hosts", ContainerlabHostViewSet)
router.register("node-mappings", NodeMappingViewSet)
router.register("interface-mappings", InterfaceMappingViewSet)
router.register("labs", LabViewSet)

urlpatterns = router.urls
