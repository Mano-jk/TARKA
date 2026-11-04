export type WorkflowEvent = {
  tool: string;
  outcome: "success" | "failure";
};

export type WorkflowRule = {
  type: "requires_prior_success";
  tool: string;
  prior_tool: string;
};

export type LogicConstraints = {
  rules?: string[];
  templates?: string[];
  bindings?: Record<string, unknown>;
  schema?: Record<string, unknown> | boolean;
  openapi?: Record<string, unknown>;
  operation_id?: string;
  workflow?: {
    history?: WorkflowEvent[];
    rules: WorkflowRule[];
  };
};

export type ChatCompletionRequest = {
  model: string;
  messages: Array<Record<string, unknown>>;
  logic_constraints?: LogicConstraints;
  [parameter: string]: unknown;
};

export type ChatCompletionResponse = Record<string, unknown>;

export class TarkaApiError extends Error {
  readonly status: number;
  readonly responseBody: string;

  constructor(status: number, responseBody: string) {
    super(`TARKA proxy returned HTTP ${status}.`);
    this.name = "TarkaApiError";
    this.status = status;
    this.responseBody = responseBody;
  }
}

export type TarkaClientOptions = {
  baseUrl: string;
  apiKey: string;
  fetchImpl?: typeof fetch;
};

export class TarkaClient {
  private readonly endpoint: URL;
  private readonly apiKey: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: TarkaClientOptions) {
    const baseUrl = new URL(options.baseUrl);
    const isLoopback =
      baseUrl.hostname === "localhost" ||
      baseUrl.hostname === "127.0.0.1" ||
      baseUrl.hostname === "[::1]";
    if (
      (baseUrl.protocol !== "https:" && !(baseUrl.protocol === "http:" && isLoopback)) ||
      baseUrl.username ||
      baseUrl.password
    ) {
      throw new TypeError("baseUrl must use HTTPS; HTTP is allowed only for loopback.");
    }
    if (!options.apiKey) {
      throw new TypeError("apiKey must not be empty.");
    }
    this.endpoint = new URL("/v1/chat/completions", baseUrl);
    this.apiKey = options.apiKey;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  async chatCompletions<TResponse extends ChatCompletionResponse = ChatCompletionResponse>(
    request: ChatCompletionRequest,
  ): Promise<TResponse> {
    const response = await this.fetchImpl(this.endpoint, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });
    const responseBody = await response.text();
    if (!response.ok) {
      throw new TarkaApiError(response.status, responseBody);
    }
    try {
      return JSON.parse(responseBody) as TResponse;
    } catch {
      throw new TarkaApiError(response.status, "The proxy returned invalid JSON.");
    }
  }
}
