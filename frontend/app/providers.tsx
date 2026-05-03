"use client";

import type { ReactNode } from "react";
import {
  FluentProvider,
  webLightTheme,
  type Theme,
} from "@fluentui/react-components";

import { LocaleProvider } from "./locale-provider";


const theme: Theme = {
  ...webLightTheme,
  colorBrandBackground: "#323130",
  colorBrandBackgroundHover: "#201f1e",
  colorBrandBackgroundPressed: "#11100f",
  colorBrandForegroundOnLight: "#323130",
  colorNeutralBackground1: "#f3f2f1",
  colorNeutralBackground2: "#ffffff",
  colorNeutralForeground1: "#201f1e",
  colorNeutralForeground2: "#3b3a39",
  colorNeutralForeground3: "#605e5c",
  colorNeutralStroke1: "#c8c6c4",
};


export function Providers({ children }: { children: ReactNode }) {
  return (
    <FluentProvider theme={theme}>
      <LocaleProvider>{children}</LocaleProvider>
    </FluentProvider>
  );
}
