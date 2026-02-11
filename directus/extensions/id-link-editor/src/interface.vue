<template>
  <div class="id-link-editor">
    <input
      class="id-link-editor__input"
      type="text"
      :value="localValue"
      :disabled="disabled"
      @input="onInput"
      @keyup.enter="openTarget"
      placeholder="ID"
    />
    <button
      class="id-link-editor__open"
      type="button"
      :disabled="disabled || !canOpen"
      @click="openTarget"
      title="Open linked card"
    >
      Open
    </button>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  value: {
    type: [String, Number, null],
    default: null,
  },
  disabled: {
    type: Boolean,
    default: false,
  },
  options: {
    type: Object,
    default: () => ({}),
  },
});

const emit = defineEmits(["input"]);

const localValue = ref(props.value == null ? "" : String(props.value));

watch(
  () => props.value,
  (next) => {
    localValue.value = next == null ? "" : String(next);
  }
);

const targetCollection = computed(() => {
  const raw = props.options?.collection;
  if (typeof raw === "string" && raw.trim()) return raw.trim();
  return "users";
});

const canOpen = computed(() => localValue.value.trim().length > 0);

function onInput(event) {
  const text = event?.target?.value ?? "";
  localValue.value = text;
  const trimmed = text.trim();
  if (!trimmed) {
    emit("input", null);
    return;
  }
  if (/^-?\d+$/.test(trimmed)) {
    emit("input", Number(trimmed));
    return;
  }
  emit("input", trimmed);
}

function openTarget() {
  if (!canOpen.value) return;
  const id = encodeURIComponent(localValue.value.trim());
  const path = `/admin/content/${targetCollection.value}/${id}`;
  if (props.options?.openInNewTab) {
    window.open(path, "_blank", "noopener");
    return;
  }
  window.location.assign(path);
}
</script>

<style scoped>
.id-link-editor {
  display: flex;
  align-items: center;
  gap: 8px;
}

.id-link-editor__input {
  width: 100%;
  min-height: 40px;
  border: 1px solid var(--theme--form--field--input--border-color, var(--theme--border-color, #2a2f3a));
  border-radius: var(--theme--border-radius, 8px);
  background: var(--theme--form--field--input--background, var(--theme--background-subdued, #101722));
  color: var(--theme--foreground, #fff);
  padding: 0 12px;
  font-size: 14px;
  outline: none;
}

.id-link-editor__input:focus {
  border-color: var(--theme--primary, #6b7cff);
}

.id-link-editor__open {
  min-width: 72px;
  height: 40px;
  border: 0;
  border-radius: var(--theme--border-radius, 8px);
  padding: 0 12px;
  font-weight: 600;
  cursor: pointer;
  color: #fff;
  background: linear-gradient(135deg, #5b7dff 0%, #6d5cff 100%);
}

.id-link-editor__open:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
