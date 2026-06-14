/**
 * Grouped settings pages — thin tab containers that reuse the existing section
 * components as tab content (no rewrite of the underlying pages). Each group is
 * routed at /settings/<group> with the active tab in ?tab=.
 */
import TabbedSettingsPage from '../../components/TabbedSettingsPage'
import Users from './Users'
import SSO from './SSO'
import Alerting from './Alerting'
import AlertRouting from './AlertRouting'
import Roles from './Roles'
import Mibs from './Mibs'
import LldpSettings from './LldpSettings'
import ComplianceTemplates from './ComplianceTemplates'
import OSVersions from './OSVersions'
import FleetInventory from './FleetInventory'
import InterfaceRules from './InterfaceRules'
import RoleConsistency from './RoleConsistency'
import CollectionHealthPanel from './CollectionHealthPanel'
import General from './General'
import Certificates from './Certificates'
import System from './System'
import AuditLog from './AuditLog'
import DataRetention from './DataRetention'

export function UsersAccessSettings() {
  return <TabbedSettingsPage tabs={[
    { id: 'users', label: 'Users', element: <Users /> },
    { id: 'sso', label: 'SSO', element: <SSO /> },
  ]} />
}

export function AlertingSettings() {
  return <TabbedSettingsPage tabs={[
    { id: 'rules', label: 'Alert Rules', element: <Alerting /> },
    { id: 'routing', label: 'Routing & Notifications', element: <AlertRouting /> },
  ]} />
}

export function NetworkDeviceSettings() {
  return <TabbedSettingsPage tabs={[
    { id: 'roles', label: 'Device Roles', element: <Roles /> },
    { id: 'mibs', label: 'MIB Files', element: <Mibs /> },
    { id: 'lldp', label: 'LLDP', element: <LldpSettings /> },
  ]} />
}

export function ComplianceSettings() {
  return <TabbedSettingsPage tabs={[
    { id: 'templates', label: 'Templates', element: <ComplianceTemplates /> },
    { id: 'os-versions', label: 'OS Versions', element: <OSVersions /> },
    { id: 'fleet-inventory', label: 'Fleet Inventory', element: <FleetInventory /> },
    { id: 'interface-rules', label: 'Interface Rules', element: <InterfaceRules /> },
    { id: 'role-consistency', label: 'Role Consistency', element: <RoleConsistency /> },
    { id: 'config-health', label: 'Config Health', element: (
      <section className="space-y-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-800 dark:text-gray-100">Config Collection Health</h2>
          <p className="text-sm text-gray-500 dark:text-gray-400">Success rate and failing devices across the fleet. Backup destinations are configured under Settings → Data Sources.</p>
        </div>
        <CollectionHealthPanel />
      </section>
    ) },
  ]} />
}

export function SystemSettings() {
  return <TabbedSettingsPage tabs={[
    { id: 'general', label: 'General', element: <General /> },
    { id: 'certificates', label: 'Certificates', element: <Certificates /> },
    { id: 'audit-log', label: 'Audit Log', element: <AuditLog /> },
    { id: 'data-retention', label: 'Data Retention', element: <DataRetention /> },
    { id: 'info', label: 'System Info', element: <System /> },
  ]} />
}
