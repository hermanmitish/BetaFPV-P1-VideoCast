# Pull in every package .mk under package/*/ in this external tree.
include $(sort $(wildcard $(BR2_EXTERNAL_ARTLYNK_BRIDGE_PATH)/package/*/*.mk))
