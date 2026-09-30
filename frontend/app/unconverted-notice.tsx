"use client";

import { MessageBar, MessageBarBody } from "@fluentui/react-components";

import { useLocale } from "./locale-provider";
import { translate } from "./messages";

export function UnconvertedTransactionsNotice({
  countLabel,
  currencies,
}: {
  countLabel: string;
  currencies: string;
}) {
  const { locale } = useLocale();
  return (
    <div data-testid="unconverted-transactions-warning">
      <MessageBar intent="warning">
        <MessageBarBody>
          {translate(locale, "unconvertedTransactionsWarning", {
            n: countLabel,
            currencies,
          })}
        </MessageBarBody>
      </MessageBar>
    </div>
  );
}
