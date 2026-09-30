export const supportedAiProviders = ["openai", "perplexity"] as const;

export type AiProvider = (typeof supportedAiProviders)[number];

export type AiProviderSettingsView = {
  provider: AiProvider;
  display_name: string;
  configured: boolean;
  selected: boolean;
  masked_api_key: string | null;
  model: string;
  created_at: string | null;
  updated_at: string | null;
};

export type AiSettingsResponse = {
  providers: AiProviderSettingsView[];
  selected_provider: AiProvider | null;
  privacy_notice: string;
};

export type AiInsightResponse = {
  provider: AiProvider;
  model: string;
  time_range: "month" | "quarter" | "6months" | "year";
  generated_at: string;
  insight_text: string;
  citations: string[];
  data_shared: string[];
  snapshot: {
    range_key: "month" | "quarter" | "6months" | "year";
    language: "en" | "zh-Hant";
    display_currency: "USD" | "TWD";
    transaction_count: number;
    date_start: string | null;
    date_end: string | null;
    totals: {
      net: number;
      spend: number;
      refunds: number;
      by_original_currency: Array<{
        currency: string;
        absolute_total: number;
      }>;
      unconverted_transaction_count?: number;
      unconverted_currencies?: string[];
    };
    top_categories: Array<{
      label: string;
      amount: number;
      share: number | null;
      count: number | null;
    }>;
    top_merchants: Array<{
      label: string;
      amount: number;
      share: number | null;
      count: number | null;
    }>;
    recurring_merchants: Array<{
      label: string;
      amount: number;
      share: number | null;
      count: number | null;
    }>;
    monthly_totals: Array<{
      month: string;
      net: number;
      spend: number;
      refunds: number;
      transaction_count: number;
    }>;
    largest_expenses: Array<{
      date: string;
      merchant: string;
      amount: number;
      currency: string;
      category: string;
    }>;
    merchant_concentration: {
      distinct_merchants: number;
      top_5_share_of_spend: number;
    };
  };
};
