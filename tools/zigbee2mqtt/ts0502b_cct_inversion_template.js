'use strict';

// Public template only. The exact device IEEE address must be injected from
// private installation configuration and must never be committed here.
const MODEL_ID = 'TS0502B';
const MANUFACTURER_NAME = '_TZ3210_3wvqjh3q';
const MIRED_SUM = 653;
const MIN_MIREDS = 153;
const MAX_MIREDS = 500;

function exactIeeeAddress(value) {
  if (typeof value !== 'string' || !/^0x[0-9a-f]{16}$/.test(value)) {
    throw new TypeError('Exact lower-case IEEE address is required');
  }
  return value;
}

function invertMired(value) {
  if (!Number.isFinite(value) || value < MIN_MIREDS || value > MAX_MIREDS) {
    throw new RangeError('TS0502B color_temp must be between 153 and 500 mired');
  }
  return MIRED_SUM - value;
}

function invertPercent(value) {
  if (!Number.isFinite(value) || value < 0 || value > 100) {
    throw new RangeError('TS0502B color_temp_percent must be between 0 and 100');
  }
  return 100 - value;
}

function invertSignedValue(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? (numeric === 0 ? 0 : -numeric) : value;
}

function invertMoveValue(value) {
  if (typeof value === 'string') {
    const normalized = value.toLowerCase();
    if (normalized === 'up' || normalized === '1') return 'down';
    if (normalized === 'down') return 'up';
    return value;
  }
  if (typeof value === 'number') return invertSignedValue(value);
  if (!value || typeof value !== 'object' || Array.isArray(value)) return value;
  const result = {...value};
  if ('rate' in result) result.rate = invertSignedValue(result.rate);
  if ('minimum' in result || 'maximum' in result) {
    const logicalMinimum = 'minimum' in result ? Number(result.minimum) : MIN_MIREDS;
    const logicalMaximum = 'maximum' in result ? Number(result.maximum) : MAX_MIREDS;
    if (
      !Number.isFinite(logicalMinimum)
      || !Number.isFinite(logicalMaximum)
      || logicalMinimum >= logicalMaximum
    ) {
      throw new RangeError('TS0502B color temperature move range is invalid');
    }
    result.minimum = invertMired(logicalMaximum);
    result.maximum = invertMired(logicalMinimum);
  }
  return result;
}

function mapMaybePromise(value, mapper) {
  return value && typeof value.then === 'function' ? value.then(mapper) : mapper(value);
}

function logicalInbound(result) {
  if (!result || typeof result !== 'object') return result;
  const logical = {...result};
  if (Number.isFinite(result.color_temp)) logical.color_temp = invertMired(result.color_temp);
  if (Number.isFinite(result.color_temp_percent)) {
    logical.color_temp_percent = invertPercent(result.color_temp_percent);
  }
  return logical;
}

function logicalOutbound(result) {
  if (!result || typeof result !== 'object') return result;
  if (!result.state || typeof result.state !== 'object') return result;
  const state = {...result.state};
  if (Number.isFinite(state.color_temp)) state.color_temp = invertMired(state.color_temp);
  if (Number.isFinite(state.color_temp_percent)) {
    state.color_temp_percent = invertPercent(state.color_temp_percent);
  }
  return {...result, state};
}

function wrapFromZigbee(converter) {
  if (!converter || typeof converter.convert !== 'function') return converter;
  return {
    ...converter,
    convert(...args) {
      return mapMaybePromise(converter.convert(...args), logicalInbound);
    },
  };
}

function wrapToZigbee(converter) {
  if (!converter || typeof converter.convertSet !== 'function') return converter;
  return {
    ...converter,
    convertSet(entity, key, value, meta) {
      let physical = value;
      let normalizeResult = false;
      if (key === 'color_temp') {
        physical = invertMired(value);
        normalizeResult = true;
      } else if (key === 'color_temp_percent') {
        physical = invertPercent(value);
        normalizeResult = true;
      } else if (key === 'color_temp_step') {
        physical = invertSignedValue(value);
      } else if (key === 'colortemp_move' || key === 'color_temp_move') {
        physical = invertMoveValue(value);
      }
      const converted = converter.convertSet(entity, key, physical, meta);
      return normalizeResult ? mapMaybePromise(converted, logicalOutbound) : converted;
    },
  };
}

function buildTs0502bCctInversion(baseDefinition, ieeeAddress) {
  if (!baseDefinition || typeof baseDefinition !== 'object') {
    throw new TypeError('Base TS0502B definition is required');
  }
  if (!Array.isArray(baseDefinition.fromZigbee) || !Array.isArray(baseDefinition.toZigbee)) {
    throw new TypeError('Base TS0502B converters are required');
  }
  const {
    zigbeeModel: _broadModelMatch,
    fingerprint: _broadFingerprint,
    fromZigbee,
    toZigbee,
    ...preserved
  } = baseDefinition;
  return {
    ...preserved,
    fingerprint: [{
      modelID: MODEL_ID,
      manufacturerName: MANUFACTURER_NAME,
      ieeeAddr: exactIeeeAddress(ieeeAddress),
    }],
    fromZigbee: fromZigbee.map(wrapFromZigbee),
    toZigbee: toZigbee.map(wrapToZigbee),
  };
}

module.exports = {
  buildTs0502bCctInversion,
  invertMired,
};
