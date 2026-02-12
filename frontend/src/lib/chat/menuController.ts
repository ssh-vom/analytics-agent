export type MenuId =
  | "provider"
  | "outputType"
  | "connectors"
  | "settings"
  | "dataContext";

export function createMenuController() {
  let activeMenu: MenuId | null = null;

  function isOpen(menu: MenuId): boolean {
    return activeMenu === menu;
  }

  function open(menu: MenuId): MenuId {
    activeMenu = menu;
    return activeMenu;
  }

  function close(menu?: MenuId): MenuId | null {
    if (!menu || activeMenu === menu) {
      activeMenu = null;
    }
    return activeMenu;
  }

  function toggle(menu: MenuId): MenuId | null {
    activeMenu = activeMenu === menu ? null : menu;
    return activeMenu;
  }

  function closeAll(): MenuId | null {
    activeMenu = null;
    return activeMenu;
  }

  function getActiveMenu(): MenuId | null {
    return activeMenu;
  }

  return {
    isOpen,
    open,
    close,
    toggle,
    closeAll,
    getActiveMenu,
  };
}
