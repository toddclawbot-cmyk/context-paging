# MyShop API — Quick Reference

Base URL: `http://localhost:8000`

## Auth

All authenticated endpoints expect `Authorization: Bearer <access_token>`.

| Endpoint | Method | Auth | Notes |
|---|---|---|---|
| `/users/register` | POST | no | returns access + refresh tokens |
| `/users/login` | POST | no | returns access + refresh tokens |
| `/users/me` | GET | yes | current user info |

## Cart

| Endpoint | Method | Auth | Notes |
|---|---|---|---|
| `/cart` | GET | yes | current cart |
| `/cart/items` | POST | yes | add an item |
| `/cart/items/{product_id}` | DELETE | yes | remove an item |
| `/cart` | DELETE | yes | clear cart |

## Orders

| Endpoint | Method | Auth | Notes |
|---|---|---|---|
| `/orders/checkout` | POST | yes | convert cart to order, decrement inventory |
| `/orders` | GET | yes | list my orders |
| `/orders/{id}/cancel` | POST | yes | cancel a pending or paid order, restock |

## Error format

```json
{ "error": "AuthError", "message": "missing bearer token" }
```
