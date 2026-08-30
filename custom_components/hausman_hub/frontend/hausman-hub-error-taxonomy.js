/* Pinned snapshot of contracts/v1/error-taxonomy.json (hausman-hub-error-taxonomy v1). */
/* Fail-closed: the panel never fetches the taxonomy over the network, decisions */
/* use only code, clientState, recoveryAction and retryPolicy, never message. */

const TAXONOMY_SNAPSHOT = {
  "contract": {
    "name": "hausman-hub-error-taxonomy",
    "version": 1
  },
  "apiMajorVersion": 1,
  "entries": [
    {
      "code": "invalid_request",
      "category": "input",
      "httpStatus": 400,
      "retryable": false,
      "clientState": "failed",
      "recoveryAction": "none",
      "retryPolicy": "never",
      "safeMessage": "Запрос не принят. Проверьте введённые данные.",
      "detailsPolicy": "discard",
      "allowedDetailKeys": [],
      "aliases": [
        {
          "surface": "voice_receipt_code",
          "code": "invalid_request"
        }
      ]
    },
    {
      "code": "forbidden",
      "category": "access",
      "httpStatus": 403,
      "retryable": false,
      "clientState": "disabled",
      "recoveryAction": "none",
      "retryPolicy": "never",
      "safeMessage": "У вас нет доступа к этому действию.",
      "detailsPolicy": "discard",
      "allowedDetailKeys": [],
      "aliases": []
    },
    {
      "code": "not_found",
      "category": "resource",
      "httpStatus": 404,
      "retryable": false,
      "clientState": "failed",
      "recoveryAction": "refresh",
      "retryPolicy": "after_refresh",
      "safeMessage": "Запрошенный объект не найден. Обновите данные.",
      "detailsPolicy": "discard",
      "allowedDetailKeys": [],
      "aliases": [
        {
          "surface": "voice_receipt_code",
          "code": "station_not_found"
        }
      ]
    },
    {
      "code": "conflict",
      "category": "conflict",
      "httpStatus": 409,
      "retryable": true,
      "clientState": "stale",
      "recoveryAction": "refresh",
      "retryPolicy": "after_refresh",
      "safeMessage": "Состояние изменилось. Обновите данные и повторите действие.",
      "detailsPolicy": "allowlisted",
      "allowedDetailKeys": [
        "expectedRevision",
        "actualRevision",
        "detailCode",
        "state",
        "recoveryRequired",
        "operatorRecoveryRequired",
        "expectedHash",
        "requestHash",
        "automaticRetryAllowed",
        "newUserActionRequired",
        "freshConfirmationRequired"
      ],
      "aliases": [
        {
          "surface": "voice_receipt_code",
          "code": "mode_changed"
        }
      ]
    },
    {
      "code": "revision_conflict",
      "category": "conflict",
      "httpStatus": 409,
      "retryable": true,
      "clientState": "stale",
      "recoveryAction": "refresh",
      "retryPolicy": "after_refresh",
      "safeMessage": "Настройки изменились на другом клиенте. Обновите данные и повторите попытку.",
      "detailsPolicy": "allowlisted",
      "allowedDetailKeys": [
        "expectedRevision",
        "actualRevision"
      ],
      "aliases": []
    },
    {
      "code": "capability_unavailable",
      "category": "capability",
      "httpStatus": 409,
      "retryable": false,
      "clientState": "disabled",
      "recoveryAction": "none",
      "retryPolicy": "never",
      "safeMessage": "Эта функция сейчас недоступна.",
      "detailsPolicy": "discard",
      "allowedDetailKeys": [],
      "aliases": [
        {
          "surface": "voice_receipt_code",
          "code": "dialog_not_supported"
        }
      ]
    },
    {
      "code": "command_not_confirmed",
      "category": "confirmation",
      "httpStatus": 409,
      "retryable": false,
      "clientState": "failed",
      "recoveryAction": "retry",
      "retryPolicy": "new_user_action",
      "safeMessage": "Устройство не подтвердило команду. Проверьте его состояние перед повтором.",
      "detailsPolicy": "allowlisted",
      "allowedDetailKeys": [
        "observedState",
        "attempts"
      ],
      "aliases": [
        {
          "surface": "climate_operation_reason",
          "code": "read_back_mismatch"
        },
        {
          "surface": "climate_operation_reason",
          "code": "confirmation_timeout"
        },
        {
          "surface": "voice_receipt_code",
          "code": "conversation_timeout"
        }
      ]
    },
    {
      "code": "climate_disabled",
      "category": "policy",
      "httpStatus": 409,
      "retryable": false,
      "clientState": "disabled",
      "recoveryAction": "none",
      "retryPolicy": "never",
      "safeMessage": "Управление климатом выключено в HausmanHub.",
      "detailsPolicy": "discard",
      "allowedDetailKeys": [],
      "aliases": [
        {
          "surface": "climate_operation_reason",
          "code": "climate_disabled"
        },
        {
          "surface": "climate_runtime_block_reason",
          "code": "climate_disabled"
        }
      ]
    },
    {
      "code": "climate_shadow_only",
      "category": "policy",
      "httpStatus": 409,
      "retryable": false,
      "clientState": "disabled",
      "recoveryAction": "none",
      "retryPolicy": "never",
      "safeMessage": "Климат работает в режиме наблюдения. Команды отключены.",
      "detailsPolicy": "discard",
      "allowedDetailKeys": [],
      "aliases": [
        {
          "surface": "climate_operation_reason",
          "code": "shadow_only"
        },
        {
          "surface": "climate_runtime_block_reason",
          "code": "shadow_only"
        }
      ]
    },
    {
      "code": "climate_state_stale",
      "category": "freshness",
      "httpStatus": 409,
      "retryable": true,
      "clientState": "stale",
      "recoveryAction": "refresh",
      "retryPolicy": "after_refresh",
      "safeMessage": "Данные о климате устарели. Обновите их перед действием.",
      "detailsPolicy": "allowlisted",
      "allowedDetailKeys": [
        "ageSeconds"
      ],
      "aliases": [
        {
          "surface": "climate_operation_reason",
          "code": "state_stale"
        },
        {
          "surface": "climate_runtime_block_reason",
          "code": "state_stale"
        }
      ]
    },
    {
      "code": "climate_authority_not_ready",
      "category": "policy",
      "httpStatus": 409,
      "retryable": false,
      "clientState": "disabled",
      "recoveryAction": "refresh",
      "retryPolicy": "new_user_action",
      "safeMessage": "Автоматическое управление климатом ещё не готово.",
      "detailsPolicy": "discard",
      "allowedDetailKeys": [],
      "aliases": [
        {
          "surface": "climate_operation_reason",
          "code": "authority_not_ready"
        },
        {
          "surface": "climate_operation_reason",
          "code": "room_not_in_canary"
        },
        {
          "surface": "climate_runtime_block_reason",
          "code": "authority_not_ready"
        },
        {
          "surface": "climate_runtime_block_reason",
          "code": "room_not_in_canary"
        }
      ]
    },
    {
      "code": "climate_cooldown",
      "category": "rate_limit",
      "httpStatus": 409,
      "retryable": false,
      "clientState": "disabled",
      "recoveryAction": "none",
      "retryPolicy": "new_user_action",
      "safeMessage": "Климатическая команда временно заблокирована. Подождите.",
      "detailsPolicy": "allowlisted",
      "allowedDetailKeys": [
        "remainingSeconds"
      ],
      "aliases": [
        {
          "surface": "climate_operation_reason",
          "code": "cooldown_active"
        },
        {
          "surface": "climate_runtime_block_reason",
          "code": "cooldown_active"
        }
      ]
    },
    {
      "code": "climate_operation_pending",
      "category": "conflict",
      "httpStatus": 409,
      "retryable": false,
      "clientState": "pending",
      "recoveryAction": "refresh",
      "retryPolicy": "never",
      "safeMessage": "Предыдущая климатическая команда ещё проверяется.",
      "detailsPolicy": "allowlisted",
      "allowedDetailKeys": [
        "operationId"
      ],
      "aliases": [
        {
          "surface": "climate_operation_reason",
          "code": "operation_pending"
        },
        {
          "surface": "climate_runtime_block_reason",
          "code": "operation_pending"
        }
      ]
    },
    {
      "code": "climate_registry_mismatch",
      "category": "conflict",
      "httpStatus": 409,
      "retryable": false,
      "clientState": "disabled",
      "recoveryAction": "refresh",
      "retryPolicy": "new_user_action",
      "safeMessage": "Настройки климатических устройств изменились. Требуется проверка.",
      "detailsPolicy": "discard",
      "allowedDetailKeys": [],
      "aliases": [
        {
          "surface": "climate_operation_reason",
          "code": "registry_mismatch"
        },
        {
          "surface": "climate_runtime_block_reason",
          "code": "registry_mismatch"
        }
      ]
    },
    {
      "code": "climate_action_unsupported",
      "category": "capability",
      "httpStatus": 409,
      "retryable": false,
      "clientState": "disabled",
      "recoveryAction": "none",
      "retryPolicy": "never",
      "safeMessage": "Это действие не поддерживается для выбранного устройства.",
      "detailsPolicy": "discard",
      "allowedDetailKeys": [],
      "aliases": [
        {
          "surface": "climate_operation_reason",
          "code": "action_unsupported"
        },
        {
          "surface": "climate_runtime_block_reason",
          "code": "action_unsupported"
        }
      ]
    },
    {
      "code": "climate_operation_not_found",
      "category": "resource",
      "httpStatus": 404,
      "retryable": false,
      "clientState": "failed",
      "recoveryAction": "refresh",
      "retryPolicy": "never",
      "safeMessage": "Климатическая операция не найдена.",
      "detailsPolicy": "discard",
      "allowedDetailKeys": [],
      "aliases": []
    },
    {
      "code": "scenario_disabled",
      "category": "policy",
      "httpStatus": 409,
      "retryable": false,
      "clientState": "disabled",
      "recoveryAction": "none",
      "retryPolicy": "never",
      "safeMessage": "Сценарий выключен и не может быть запущен.",
      "detailsPolicy": "discard",
      "allowedDetailKeys": [],
      "aliases": []
    },
    {
      "code": "rate_limited",
      "category": "rate_limit",
      "httpStatus": 429,
      "retryable": true,
      "clientState": "disabled",
      "recoveryAction": "none",
      "retryPolicy": "after_delay",
      "safeMessage": "Слишком много запросов. Подождите перед повтором.",
      "detailsPolicy": "allowlisted",
      "allowedDetailKeys": [
        "retryAfterSeconds"
      ],
      "aliases": []
    },
    {
      "code": "unavailable",
      "category": "availability",
      "httpStatus": 503,
      "retryable": true,
      "clientState": "offline",
      "recoveryAction": "reconnect",
      "retryPolicy": "read_only",
      "safeMessage": "HausmanHub временно недоступен. Проверьте подключение и повторите позже.",
      "detailsPolicy": "discard",
      "allowedDetailKeys": [],
      "aliases": [
        {
          "surface": "climate_operation_reason",
          "code": "device_unavailable"
        },
        {
          "surface": "climate_runtime_block_reason",
          "code": "device_unavailable"
        },
        {
          "surface": "voice_receipt_code",
          "code": "station_unavailable"
        },
        {
          "surface": "voice_receipt_code",
          "code": "summary_unavailable"
        },
        {
          "surface": "voice_receipt_code",
          "code": "provider_error"
        }
      ]
    },
    {
      "code": "not_acceptable",
      "category": "capability",
      "httpStatus": 406,
      "retryable": false,
      "clientState": "disabled",
      "recoveryAction": "update_client",
      "retryPolicy": "never",
      "safeMessage": "Формат ответа не поддерживается. Обновите клиент.",
      "detailsPolicy": "discard",
      "allowedDetailKeys": [],
      "aliases": []
    },
    {
      "code": "internal_error",
      "category": "internal",
      "httpStatus": 500,
      "retryable": false,
      "clientState": "failed",
      "recoveryAction": "retry",
      "retryPolicy": "new_user_action",
      "safeMessage": "Не удалось завершить действие из-за внутренней ошибки.",
      "detailsPolicy": "discard",
      "allowedDetailKeys": [],
      "aliases": [
        {
          "surface": "climate_operation_reason",
          "code": "internal_error"
        }
      ]
    }
  ],
  "detailPolicies": [
    {
      "baseCode": "conflict",
      "detailCode": "idempotency_in_progress",
      "retryable": false,
      "clientState": "pending",
      "recoveryAction": "refresh",
      "retryPolicy": "read_only",
      "safeMessage": "Команда уже обрабатывается. Обновите состояние, не повторяя действие автоматически.",
      "automaticRetryAllowed": false,
      "newUserActionRequired": false,
      "freshConfirmationRequired": false
    },
    {
      "baseCode": "conflict",
      "detailCode": "idempotency_key_conflict",
      "retryable": false,
      "clientState": "failed",
      "recoveryAction": "retry",
      "retryPolicy": "new_user_action",
      "safeMessage": "Ключ команды уже использован для другого запроса. Запустите действие заново вручную.",
      "automaticRetryAllowed": false,
      "newUserActionRequired": true,
      "freshConfirmationRequired": true
    }
  ]
};

const ERROR_CONTRACT_NAME = "hausman-hub-error";
const FALLBACK_CODE = "internal_error";
const ENTRY_BY_CODE = new Map(TAXONOMY_SNAPSHOT.entries.map((entry) => [entry.code, entry]));
const DETAIL_POLICY_BY_KEY = new Map(
  (TAXONOMY_SNAPSHOT.detailPolicies || []).map(
    (policy) => [`${policy.baseCode}:${policy.detailCode}`, policy],
  ),
);

/* Legacy routes without the canonical envelope still map by status only, */
/* never by the free-form message text. */
const STATUS_FALLBACK_CODES = {
  400: "invalid_request",
  403: "forbidden",
  404: "not_found",
  406: "not_acceptable",
  409: "conflict",
  422: "invalid_request",
  429: "rate_limited",
  503: "unavailable",
};

export function taxonomyEntry(code) {
  return ENTRY_BY_CODE.get(code) || null;
}

export function taxonomyAliasEntry(surface, code) {
  if (typeof surface !== "string" || typeof code !== "string") return null;
  for (const entry of TAXONOMY_SNAPSHOT.entries) {
    const aliases = Array.isArray(entry.aliases) ? entry.aliases : [];
    if (aliases.some((alias) => alias.surface === surface && alias.code === code)) return entry;
  }
  return null;
}

function sanitizeDetails(entry, rawDetails) {
  if (!rawDetails || typeof rawDetails !== "object" || entry.detailsPolicy !== "allowlisted") {
    return {};
  }
  const allowed = new Set(Array.isArray(entry.allowedDetailKeys) ? entry.allowedDetailKeys : []);
  const details = {};
  Object.keys(rawDetails).forEach((key) => {
    if (allowed.has(key)) details[key] = rawDetails[key];
  });
  return details;
}

function policyFromEntry(entry, rawDetails) {
  return {
    code: entry.code,
    clientState: entry.clientState,
    recoveryAction: entry.recoveryAction,
    retryPolicy: entry.retryPolicy,
    retryable: entry.retryable === true,
    safeMessage: entry.safeMessage,
    details: sanitizeDetails(entry, rawDetails),
    automaticRetryAllowed: entry.automaticRetryAllowed === true,
    newUserActionRequired: entry.newUserActionRequired === true,
    freshConfirmationRequired: entry.freshConfirmationRequired === true,
  };
}

function policyForCanonicalBody(entry, body) {
  const detailCode = body.details && typeof body.details.detailCode === "string"
    ? body.details.detailCode : "";
  const detailPolicy = DETAIL_POLICY_BY_KEY.get(`${entry.code}:${detailCode}`);
  if (!detailPolicy) return policyFromEntry(entry, body.details);
  return policyFromEntry(
    {
      ...entry,
      ...detailPolicy,
      code: entry.code,
      detailsPolicy: entry.detailsPolicy,
      allowedDetailKeys: entry.allowedDetailKeys,
    },
    body.details,
  );
}

function errorStatus(error) {
  if (!error || typeof error !== "object") return 0;
  const status = Number(error.status !== undefined ? error.status : error.status_code);
  return Number.isInteger(status) ? status : 0;
}

export function resolveApiError(error) {
  const body = error && typeof error === "object" ? error.body : null;
  if (body && typeof body === "object" && body.contract
      && body.contract.name === ERROR_CONTRACT_NAME && body.contract.version === 1) {
    const entry = typeof body.code === "string" ? taxonomyEntry(body.code) : null;
    if (entry) return policyForCanonicalBody(entry, body);
    /* Unknown canonical code fails closed to internal_error without details. */
    return policyFromEntry(ENTRY_BY_CODE.get(FALLBACK_CODE), null);
  }
  const fallback = taxonomyEntry(STATUS_FALLBACK_CODES[errorStatus(error)]);
  return policyFromEntry(fallback || ENTRY_BY_CODE.get(FALLBACK_CODE), null);
}

export function apiErrorMessage(error) {
  return resolveApiError(error).safeMessage;
}

export function resolveClimateReceipt(receipt) {
  if (!receipt || typeof receipt !== "object" || receipt.confirmed === true) return null;
  const entry = (typeof receipt.reason === "string"
      && taxonomyAliasEntry("climate_operation_reason", receipt.reason))
    || ENTRY_BY_CODE.get(FALLBACK_CODE);
  const policy = policyFromEntry(entry, null);
  policy.operationId = typeof receipt.operation_id === "string" ? receipt.operation_id : null;
  policy.receiptStatus = typeof receipt.status === "string" ? receipt.status : "";
  return policy;
}

/* Automatic retry is allowed only for read-only requests of read_only policy. */
/* A physical command is never repeated automatically. */
export function automaticRetryAllowed(policy, operationKind) {
  return Boolean(policy)
    && operationKind === "read"
    && policy.retryPolicy === "read_only";
}

export function requiresSnapshotRefresh(policy) {
  return Boolean(policy)
    && (policy.recoveryAction === "refresh" || policy.retryPolicy === "after_refresh");
}

export function pendingOperationId(policy) {
  if (!policy || policy.clientState !== "pending") return null;
  const fromDetails = policy.details && typeof policy.details.operationId === "string"
    ? policy.details.operationId : null;
  return fromDetails || policy.operationId || null;
}

export const ERROR_TAXONOMY_SNAPSHOT = TAXONOMY_SNAPSHOT;
