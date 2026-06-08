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
import ComplianceTemplates from './ComplianceTemplates'
import OSVersions from './OSVersions'
import FleetInventory from './FleetInventory'
import General from './General'
import Certificates from './Certificates'
import System from './System'

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
  ]} />
}

export function ComplianceSettings() {
  return <TabbedSettingsPage tabs={[
    { id: 'templates', label: 'Templates', element: <ComplianceTemplates /> },
    { id: 'os-versions', label: 'OS Versions', element: <OSVersions /> },
    { id: 'fleet-inventory', label: 'Fleet Inventory', element: <FleetInventory /> },
  ]} />
}

export function SystemSettings() {
  return <TabbedSettingsPage tabs={[
    { id: 'general', label: 'General', element: <General /> },
    { id: 'certificates', label: 'Certificates', element: <Certificates /> },
    { id: 'info', label: 'System Info', element: <System /> },
  ]} />
}
