import { ReactNode, useEffect, useRef } from "react";
import notice from "../../NOTICE?raw";
import { Icon, Logo, MAX_FILES, SOURCE_URL } from "./shared";

const ISSUES_URL = `${SOURCE_URL}/issues`;

export function Closed() {
  // The service can close while another screen is open, so focus and the tab title move here as they do for any screen.
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
    document.title = "Service closed · LocalFileTools";
  }, []);
  return (
    <div className="message card">
      <Logo />
      <div>
        <h1 ref={heading} tabIndex={-1}>Service closed</h1>
        <p className="muted">LocalFileTools is closed at the moment. Please try again later.</p>
      </div>
    </div>
  );
}

export function NotFound() {
  return (
    <div className="message card">
      <Logo />
      <div>
        <h1 tabIndex={-1}>Page not found</h1>
        <p className="muted">There is nothing at this address.</p>
      </div>
      <a className="btn secondary" href="/#">
        Go to the home page
      </a>
    </div>
  );
}

function Doc({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="doc">
      <a className="back" href="#">
        <Icon name="back" />
        All Tools
      </a>
      <h1 tabIndex={-1}>{title}</h1>
      {children}
    </div>
  );
}

export function Privacy() {
  return (
    <Doc title="Privacy and terms">
      <p>LocalFileTools is a free, non-commercial service run by one person.</p>

      <h2>Your files</h2>
      <p>Files you upload are processed on this server by open-source software. They are not passed on to any other service.</p>
      <p>
        Your files and the results are deleted automatically about 15 minutes after a job finishes. You can delete them sooner with
        the “Delete my files now” button.
      </p>
      <p>Only the browser that uploaded a file can download its results.</p>

      <h2>Cookie</h2>
      <p>
        The site sets one cookie, named session. It holds a random value that ties your jobs to this browser, so that nobody else can
        download them. The service does not work without it. It expires after 7 days. There are no tracking cookies, no analytics and
        no advertising.
      </p>

      <h2>Cloudflare</h2>
      <p>
        The site is reached through Cloudflare. Everything you send, including your files, passes through Cloudflare's network on its
        way to this server, and Cloudflare sees your IP address. Cloudflare handles this data under its own{" "}
        <a href="https://www.cloudflare.com/privacypolicy/" rel="noreferrer">
          privacy policy
        </a>
        .
      </p>

      <h2>Logs</h2>
      <p>
        The server keeps a short log of requests and job numbers to find faults. The log does not hold your IP address or the
        contents of your files.
      </p>

      <h2>Terms</h2>
      <ul>
        <li>The service is provided as it is, without any warranty. It can be changed or closed at any time.</li>
        <li>Keep your originals. The service keeps no copies once files are deleted.</li>
        <li>Upload only files you have the right to use, and nothing illegal.</li>
        <li>Do not upload documents whose exposure you could not afford.</li>
        <li>Limits per job: up to 50 MB in total and {MAX_FILES} files.</li>
      </ul>

      <h2>Contact</h2>
      <p>
        Questions and problems:{" "}
        <a href={ISSUES_URL} rel="noreferrer">
          GitHub issues
        </a>
      </p>
    </Doc>
  );
}

export function Licences() {
  return (
    <Doc title="Licences">
      <p>
        LocalFileTools is free software under the GNU Affero General Public License, version 3.{" "}
        <a href={SOURCE_URL} rel="noreferrer">
          Source code
        </a>
      </p>
      <pre tabIndex={0} role="region" aria-label="Third-party software licences">
        {notice}
      </pre>
    </Doc>
  );
}
