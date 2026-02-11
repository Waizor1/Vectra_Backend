import { defineInterface } from "@directus/extensions-sdk";

import InterfaceComponent from "./interface.vue";

export default defineInterface({
  id: "id-link-editor",
  name: "ID Link Editor",
  icon: "open_in_new",
  description: "Edit related item ID and open target card",
  component: InterfaceComponent,
  types: ["bigInteger", "integer", "string"],
  options: [
    {
      field: "collection",
      name: "Target Collection",
      type: "string",
      meta: {
        interface: "input",
        width: "half",
        options: {
          placeholder: "users",
          trim: true,
        },
      },
      schema: {
        default_value: "users",
      },
    },
    {
      field: "openInNewTab",
      name: "Open In New Tab",
      type: "boolean",
      meta: {
        interface: "boolean",
        width: "half",
      },
      schema: {
        default_value: false,
      },
    },
  ],
});
