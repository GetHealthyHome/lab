export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.18"
  }
  public: {
    Tables: {
      assets: {
        Row: {
          barcode: string | null
          brand: string | null
          condition: string
          created_at: string
          due_at: string | null
          holder_id: string | null
          home_location_id: string | null
          id: string
          kind: string
          kit_id: string | null
          last_checked_out_at: string | null
          location_id: string | null
          name: string
          notes: string | null
          photo_url: string | null
          quantity: number
          serial: string | null
          status: string
        }
        Insert: {
          barcode?: string | null
          brand?: string | null
          condition?: string
          created_at?: string
          due_at?: string | null
          holder_id?: string | null
          home_location_id?: string | null
          id?: string
          kind?: string
          kit_id?: string | null
          last_checked_out_at?: string | null
          location_id?: string | null
          name: string
          notes?: string | null
          photo_url?: string | null
          quantity?: number
          serial?: string | null
          status?: string
        }
        Update: {
          barcode?: string | null
          brand?: string | null
          condition?: string
          created_at?: string
          due_at?: string | null
          holder_id?: string | null
          home_location_id?: string | null
          id?: string
          kind?: string
          kit_id?: string | null
          last_checked_out_at?: string | null
          location_id?: string | null
          name?: string
          notes?: string | null
          photo_url?: string | null
          quantity?: number
          serial?: string | null
          status?: string
        }
        Relationships: [
          {
            foreignKeyName: "assets_holder_id_fkey"
            columns: ["holder_id"]
            isOneToOne: false
            referencedRelation: "people"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "assets_home_location_id_fkey"
            columns: ["home_location_id"]
            isOneToOne: false
            referencedRelation: "locations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "assets_kit_id_fkey"
            columns: ["kit_id"]
            isOneToOne: false
            referencedRelation: "assets"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "assets_location_id_fkey"
            columns: ["location_id"]
            isOneToOne: false
            referencedRelation: "locations"
            referencedColumns: ["id"]
          },
        ]
      }
      locations: {
        Row: {
          code: string
          created_at: string
          id: string
          name: string
          parent_id: string | null
        }
        Insert: {
          code: string
          created_at?: string
          id?: string
          name: string
          parent_id?: string | null
        }
        Update: {
          code?: string
          created_at?: string
          id?: string
          name?: string
          parent_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "locations_parent_id_fkey"
            columns: ["parent_id"]
            isOneToOne: false
            referencedRelation: "locations"
            referencedColumns: ["id"]
          },
        ]
      }
      movements: {
        Row: {
          action: string
          asset_id: string
          condition: string | null
          created_at: string
          created_by: string | null
          due_at: string | null
          id: string
          location_id: string | null
          note: string | null
          person_id: string | null
        }
        Insert: {
          action: string
          asset_id: string
          condition?: string | null
          created_at?: string
          created_by?: string | null
          due_at?: string | null
          id?: string
          location_id?: string | null
          note?: string | null
          person_id?: string | null
        }
        Update: {
          action?: string
          asset_id?: string
          condition?: string | null
          created_at?: string
          created_by?: string | null
          due_at?: string | null
          id?: string
          location_id?: string | null
          note?: string | null
          person_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "movements_asset_id_fkey"
            columns: ["asset_id"]
            isOneToOne: false
            referencedRelation: "assets"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "movements_location_id_fkey"
            columns: ["location_id"]
            isOneToOne: false
            referencedRelation: "locations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "movements_person_id_fkey"
            columns: ["person_id"]
            isOneToOne: false
            referencedRelation: "people"
            referencedColumns: ["id"]
          },
        ]
      }
      people: {
        Row: {
          active: boolean
          auth_user_id: string | null
          badge_code: string | null
          created_at: string
          email: string | null
          id: string
          name: string
          role: string
        }
        Insert: {
          active?: boolean
          auth_user_id?: string | null
          badge_code?: string | null
          created_at?: string
          email?: string | null
          id?: string
          name: string
          role?: string
        }
        Update: {
          active?: boolean
          auth_user_id?: string | null
          badge_code?: string | null
          created_at?: string
          email?: string | null
          id?: string
          name?: string
          role?: string
        }
        Relationships: []
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      record_movement: {
        Args: {
          p_action: string
          p_asset_id: string
          p_condition?: string
          p_due_at?: string
          p_location_id?: string
          p_missing_ids?: string[]
          p_note?: string
          p_person_id?: string
        }
        Returns: undefined
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

export type Tables<T extends keyof Database["public"]["Tables"]> = Database["public"]["Tables"][T]["Row"]
export type Asset = Tables<"assets">
export type Person = Tables<"people">
export type Location = Tables<"locations">
export type Movement = Tables<"movements">
export type MovementArgs = Database["public"]["Functions"]["record_movement"]["Args"]
