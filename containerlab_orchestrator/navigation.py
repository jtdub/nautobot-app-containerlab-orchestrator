"""Navigation menu items for the containerlab_orchestrator app."""

from nautobot.apps.ui import (
    NavigationIconChoices,
    NavigationWeightChoices,
    NavMenuAddButton,
    NavMenuGroup,
    NavMenuItem,
    NavMenuTab,
)

menu_items = (
    NavMenuTab(
        name="Containerlab",
        weight=NavigationWeightChoices.DESIGN + 50,
        icon=NavigationIconChoices.DESIGN,
        groups=(
            NavMenuGroup(
                name="Hosts",
                weight=100,
                items=(
                    NavMenuItem(
                        link="plugins:containerlab_orchestrator:containerlabhost_list",
                        name="Containerlab Hosts",
                        permissions=["containerlab_orchestrator.view_containerlabhost"],
                        buttons=(
                            NavMenuAddButton(
                                link="plugins:containerlab_orchestrator:containerlabhost_add",
                                permissions=["containerlab_orchestrator.add_containerlabhost"],
                            ),
                        ),
                    ),
                ),
            ),
            NavMenuGroup(
                name="Mappings",
                weight=200,
                items=(
                    NavMenuItem(
                        link="plugins:containerlab_orchestrator:nodemapping_list",
                        name="Node Mappings",
                        permissions=["containerlab_orchestrator.view_nodemapping"],
                        buttons=(
                            NavMenuAddButton(
                                link="plugins:containerlab_orchestrator:nodemapping_add",
                                permissions=["containerlab_orchestrator.add_nodemapping"],
                            ),
                        ),
                    ),
                ),
            ),
        ),
    ),
)
