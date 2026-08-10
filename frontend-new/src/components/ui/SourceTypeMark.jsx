import cn from "../../lib/cn";
import mysqlIcon from "../../assets/db-icons/mysql.svg";
import postgresqlIcon from "../../assets/db-icons/postgresql.svg";
import sqlserverIcon from "../../assets/db-icons/sqlserver.svg";
import oracleIcon from "../../assets/db-icons/oracle.svg";

const sourceIcons = {
  mysql: mysqlIcon,
  postgresql: postgresqlIcon,
  sqlserver: sqlserverIcon,
  oracle: oracleIcon,
};

export default function SourceTypeMark({ type, className = "", imageClassName = "" }) {
  const icon = sourceIcons[type?.toLowerCase()];

  return (
    <span className={cn("grid h-9 w-9 shrink-0 select-none place-items-center rounded-control border border-line bg-surface", className)}>
      {icon
        ? <img src={icon} alt="" className={cn("h-5 w-5 object-contain", imageClassName)} />
        : <span className="font-mono text-[11px] font-bold text-muted">DB</span>}
    </span>
  );
}
