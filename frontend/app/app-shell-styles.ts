import { makeStyles, shorthands, tokens } from "@fluentui/react-components";

/** Shared responsive app shell used across authenticated pages. */
export const useAppShellStyles = makeStyles({
  page: {
    minHeight: "100dvh",
    backgroundColor: "var(--page-surface)",
  },
  shell: {
    maxWidth: "1480px",
    marginLeft: "auto",
    marginRight: "auto",
    paddingTop: "max(20px, calc(env(safe-area-inset-top) + 16px))",
    paddingBottom: "max(28px, calc(env(safe-area-inset-bottom) + 20px))",
    paddingLeft: "max(20px, calc(env(safe-area-inset-left) + 16px))",
    paddingRight: "max(20px, calc(env(safe-area-inset-right) + 16px))",
    display: "grid",
    gridTemplateColumns: "280px minmax(0, 1fr)",
    gap: "16px",
    alignItems: "start",
    "@media (min-width: 1280px)": {
      gridTemplateColumns: "320px minmax(0, 1fr)",
      gap: "20px",
    },
    "@media (max-width: 959px)": {
      gridTemplateColumns: "1fr",
    },
    "@media (max-width: 639px)": {
      paddingLeft: "max(16px, calc(env(safe-area-inset-left) + 12px))",
      paddingRight: "max(16px, calc(env(safe-area-inset-right) + 12px))",
    },
  },
  sidebar: {
    display: "flex",
    flexDirection: "column",
    gap: "16px",
    position: "sticky",
    top: "max(16px, calc(env(safe-area-inset-top) + 8px))",
    alignSelf: "start",
    "@media (max-width: 959px)": {
      position: "static",
    },
  },
  contentColumn: {
    display: "flex",
    flexDirection: "column",
    gap: "16px",
    minWidth: 0,
  },
  sidebarNavLink: {
    display: "block",
    paddingTop: "12px",
    paddingBottom: "12px",
    paddingLeft: "12px",
    paddingRight: "12px",
    fontWeight: tokens.fontWeightSemibold,
    color: tokens.colorNeutralForeground2,
    backgroundColor: tokens.colorNeutralBackground2,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius(tokens.borderRadiusMedium),
    ":hover": {
      backgroundColor: tokens.colorSubtleBackgroundHover,
      color: tokens.colorNeutralForeground1,
    },
  },
});
