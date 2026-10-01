export type EditableProviderDraft = {
  apiKey: string;
  model: string;
  saving: boolean;
  removing: boolean;
};

export function mergeProviderDraft(
  current: EditableProviderDraft | undefined,
  serverModel: string,
  previousServerModel: string | null,
  resetSecrets: boolean
): EditableProviderDraft {
  if (resetSecrets || current === undefined) {
    return {
      apiKey: "",
      model: serverModel,
      saving: false,
      removing: false,
    };
  }
  const modelIsUntouched =
    previousServerModel === null
      ? current.model === ""
      : current.model === previousServerModel;
  return {
    apiKey: current.apiKey,
    model: modelIsUntouched ? serverModel : current.model,
    saving: current.saving,
    removing: current.removing,
  };
}

export function keepProviderSelection<T extends string>(
  current: T | "",
  providers: readonly { provider: T; configured: boolean }[],
  selectedProvider: T | null
): T | "" {
  const stillConfigured = providers.some(
    (provider) => provider.provider === current && provider.configured
  );
  if (current && stillConfigured) {
    return current;
  }
  if (
    selectedProvider &&
    providers.some(
      (provider) =>
        provider.provider === selectedProvider && provider.configured
    )
  ) {
    return selectedProvider;
  }
  return providers.find((provider) => provider.configured)?.provider ?? "";
}
