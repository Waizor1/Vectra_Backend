import { defineInterface } from "@directus/extensions-sdk";

import InterfaceComponent from "./interface.vue";

export default defineInterface({
  id: "tvpn-user-card",
  name: "Vectra User Card",
  icon: "person",
  description:
    "Богатая карточка пользователя: способы входа, подписка, финансы, устройства, активность, риск-индикаторы.",
  component: InterfaceComponent,
  hideLabel: true,
  // Bind to the user id (bigInteger) to render the card on the users.id field,
  // or use as a 'presentation' alias on a virtual field. Keep types broad so the
  // user can attach the interface either to `id` or to a presentation field.
  types: ["alias", "bigInteger", "integer", "string"],
  groups: ["presentation", "standard"],
  options: [
    {
      field: "endpoint",
      name: "Endpoint Path",
      type: "string",
      meta: {
        interface: "input",
        width: "full",
        options: {
          placeholder: "/admin-widgets/user-card",
          trim: true,
        },
        note: "Базовый путь admin-widgets endpoint. ID пользователя добавляется автоматически.",
      },
      schema: {
        default_value: "/admin-widgets/user-card",
      },
    },
    {
      field: "showRawJson",
      name: "Показать raw JSON",
      type: "boolean",
      meta: {
        interface: "boolean",
        width: "half",
        note: "Дополнительная сворачиваемая секция с сырым ответом для отладки.",
      },
      schema: {
        default_value: true,
      },
    },
  ],
});
