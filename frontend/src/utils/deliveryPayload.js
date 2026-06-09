export function buildCustomDeliveryPayload(deliveryMode, deliveryStandards) {
  if (deliveryMode === 'custom') {
    return { delivery_standards: deliveryStandards };
  }
  return {};
}
